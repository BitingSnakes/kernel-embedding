import csv
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
from pathlib import Path

import tree_sitter_c as tsc
import tree_sitter_python as tsp
import tree_sitter_rust as tsr
from tokenizers import Tokenizer
from tree_sitter import Language, Parser, Query, QueryCursor

QUERY_STRINGS = {
    ".c": """
(function_definition) @function
(struct_specifier) @struct
(preproc_def) @macro
""",
    ".h": """
(function_definition) @function
(struct_specifier) @struct
(preproc_def) @macro
""",
    ".rs": """
(function_item) @function
(struct_item) @struct
(enum_item) @enum
(trait_item) @trait
(impl_item) @impl
(macro_definition) @macro
""",
    ".py": """
(function_definition) @function
(class_definition) @class
(decorated_definition) @decorated
""",
}
ASM_EXTENSIONS = {".S", ".asm", ".s"}
SHELL_EXTENSIONS = {".bash", ".ksh", ".sh", ".zsh"}
ASM_SYMBOL_MACROS = {
    "ENTRY",
    "GLOBAL",
    "SYM_CODE_START",
    "SYM_CODE_START_LOCAL",
    "SYM_DATA_START",
    "SYM_DATA_START_LOCAL",
    "SYM_FUNC_START",
    "SYM_FUNC_START_LOCAL",
    "SYM_FUNC_START_WEAK",
    "WEAK",
}
COST_PER_1M_TOKENS = 0.15
GEMMA_TOKENIZER_ID = os.environ.get("GEMMA_TOKENIZER_ID", "google/gemma-4-26B-A4B")
GEMMA_TOKENIZER_REVISION = os.environ.get("GEMMA_TOKENIZER_REVISION", "main")

_worker_parsers = None
_worker_query_cursors = None
_worker_tokenizer = None


def format_cost(token_count: int) -> str:
    return f"${(token_count / 1_000_000) * COST_PER_1M_TOKENS:.6f}"


def supported_extensions():
    return (
        tuple(QUERY_STRINGS)
        + tuple(sorted(ASM_EXTENSIONS))
        + tuple(sorted(SHELL_EXTENSIONS))
    )


def build_tokenizer():
    tokenizer_path = Path(GEMMA_TOKENIZER_ID)
    if tokenizer_path.exists():
        return Tokenizer.from_file(str(tokenizer_path))

    return Tokenizer.from_pretrained(
        GEMMA_TOKENIZER_ID,
        revision=GEMMA_TOKENIZER_REVISION,
        token=os.environ.get("HF_TOKEN"),
    )


def build_parser(language_ptr):
    """Initializes a Tree-sitter language parser."""
    language = Language(language_ptr)
    parser = Parser(language)
    return parser, language


def init_worker():
    """Initializes parser state once per worker process."""
    global _worker_parsers, _worker_query_cursors, _worker_tokenizer

    c_parser, c_lang = build_parser(tsc.language())
    python_parser, python_lang = build_parser(tsp.language())
    rust_parser, rust_lang = build_parser(tsr.language())

    _worker_parsers = {
        ".c": c_parser,
        ".h": c_parser,
        ".py": python_parser,
        ".rs": rust_parser,
    }
    languages = {
        ".c": c_lang,
        ".h": c_lang,
        ".py": python_lang,
        ".rs": rust_lang,
    }
    _worker_query_cursors = {
        extension: QueryCursor(Query(languages[extension], query_string))
        for extension, query_string in QUERY_STRINGS.items()
    }
    _worker_tokenizer = build_tokenizer()


def count_chunk(file_path: Path, source_code: bytes, start_byte: int, end_byte: int, kind: str):
    chunk_text = source_code[start_byte:end_byte].decode("utf-8", errors="ignore")
    contextualized_text = (
        f"// File: {file_path.as_posix()}\n"
        f"// Type: {kind}\n"
        f"{chunk_text}"
    )
    char_count = len(contextualized_text)
    token_count = len(
        _worker_tokenizer.encode(contextualized_text, add_special_tokens=False).ids
    )
    return char_count, token_count


def asm_chunk_starts(source_code: bytes):
    starts = []
    offset = 0
    label_pattern = re.compile(rb"^\s*([A-Za-z_.$][\w.$@]*)\s*:")
    macro_pattern = re.compile(rb"^\s*([A-Z_][A-Z0-9_]*)\s*\(([^,)]+)")
    asm_macro_pattern = re.compile(rb"^\s*\.macro\s+([A-Za-z_.$][\w.$@]*)")

    for line in source_code.splitlines(keepends=True):
        macro_match = macro_pattern.match(line)
        if macro_match and macro_match.group(1).decode() in ASM_SYMBOL_MACROS:
            starts.append((offset, "asm_symbol"))
        elif asm_macro_pattern.match(line):
            starts.append((offset, "asm_macro"))
        else:
            label_match = label_pattern.match(line)
            if label_match:
                label = label_match.group(1)
                if not label[:1].isdigit() and not label.startswith(b".L"):
                    starts.append((offset, "asm_label"))

        offset += len(line)

    return starts


def process_asm_file(file_path: Path, source_code: bytes):
    starts = asm_chunk_starts(source_code)
    if not starts and source_code:
        starts = [(0, "asm_file")]

    file_char_count = 0
    file_token_count = 0
    file_chunk_count = 0

    for index, (start_byte, kind) in enumerate(starts):
        end_byte = starts[index + 1][0] if index + 1 < len(starts) else len(source_code)
        char_count, token_count = count_chunk(
            file_path, source_code, start_byte, end_byte, kind
        )

        file_char_count += char_count
        file_token_count += token_count
        file_chunk_count += 1

    return file_chunk_count, file_char_count, file_token_count


def is_shell_file(file_path: Path):
    if file_path.suffix in SHELL_EXTENSIONS:
        return True

    try:
        with open(file_path, "rb") as f:
            first_line = f.readline(256)
    except OSError:
        return False

    return first_line.startswith(b"#!") and any(
        shell in first_line
        for shell in (b"/sh", b"/bash", b"/dash", b"/zsh", b"/ksh", b"env sh")
    )


def shell_chunk_starts(source_code: bytes):
    starts = []
    offset = 0
    function_patterns = (
        re.compile(rb"^\s*(?:function\s+)?([A-Za-z_][\w.-]*)\s*\(\s*\)\s*\{?"),
        re.compile(rb"^\s*function\s+([A-Za-z_][\w.-]*)\s*\{?"),
    )

    for line in source_code.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(b"#"):
            offset += len(line)
            continue

        if any(pattern.match(line) for pattern in function_patterns):
            starts.append((offset, "shell_function"))

        offset += len(line)

    return starts


def process_shell_file(file_path: Path, source_code: bytes):
    starts = shell_chunk_starts(source_code)
    if not starts and source_code:
        starts = [(0, "shell_script")]

    file_char_count = 0
    file_token_count = 0
    file_chunk_count = 0

    for index, (start_byte, kind) in enumerate(starts):
        end_byte = starts[index + 1][0] if index + 1 < len(starts) else len(source_code)
        char_count, token_count = count_chunk(
            file_path, source_code, start_byte, end_byte, kind
        )

        file_char_count += char_count
        file_token_count += token_count
        file_chunk_count += 1

    return file_chunk_count, file_char_count, file_token_count


def process_source_file(file_path: Path):
    """Calculates embedding extraction metrics for one source file."""
    if _worker_parsers is None:
        init_worker()

    try:
        # Read as binary and forcefully ignore decode errors to bypass truncated multibyte crashes
        with open(file_path, "rb") as f:
            source_code = f.read()

        file_char_count = 0
        file_token_count = 0
        file_chunk_count = 0

        if file_path.suffix in ASM_EXTENSIONS:
            file_chunk_count, file_char_count, file_token_count = process_asm_file(
                file_path, source_code
            )
        elif is_shell_file(file_path):
            file_chunk_count, file_char_count, file_token_count = process_shell_file(
                file_path, source_code
            )
        else:
            parser = _worker_parsers[file_path.suffix]
            query_cursor = _worker_query_cursors[file_path.suffix]

            tree = parser.parse(source_code)
            captures = query_cursor.captures(tree.root_node)

            if isinstance(captures, dict):
                items = [
                    (node, name) for name, nodes in captures.items() for node in nodes
                ]
            else:
                items = captures

            for node, capture_name in items:
                char_count, token_count = count_chunk(
                    file_path, source_code, node.start_byte, node.end_byte, capture_name
                )

                file_char_count += char_count
                file_token_count += token_count
                file_chunk_count += 1

        row = [
            file_path.as_posix(),
            file_chunk_count,
            file_char_count,
            file_token_count,
            format_cost(file_token_count),
        ]

        return row, file_chunk_count, file_char_count, file_token_count, None
    except Exception as e:
        return None, 0, 0, 0, f"Failed to process {file_path}: {e}"


def process_pool_context():
    """Uses fork on POSIX so callers from stdin/notebooks can still spawn workers."""
    try:
        return get_context("fork")
    except ValueError:
        return None


def should_report_progress(processed_files: int, total_files: int) -> bool:
    if processed_files in {1, total_files}:
        return True

    progress_step = max(1, total_files // 20)
    return processed_files % progress_step == 0


def print_progress(
    processed_files: int,
    total_files: int,
    total_chars: int,
    total_tokens: int,
    total_chunks: int,
    start_time: float,
):
    elapsed_seconds = time.monotonic() - start_time
    files_per_second = processed_files / elapsed_seconds if elapsed_seconds else 0
    estimated_total_tokens = int(total_tokens * total_files / processed_files)
    percent_done = (processed_files / total_files) * 100

    print(
        f"Progress: {processed_files}/{total_files} files "
        f"({percent_done:.1f}%) | "
        f"chunks: {total_chunks} | "
        f"cost so far: {format_cost(total_tokens)} | "
        f"estimated total cost: {format_cost(estimated_total_tokens)} | "
        f"{files_per_second:.1f} files/s"
    )


def process_kernel_directory(
    target_directory: str, output_csv_path: str, workers: int | None = None
):
    """
    Recursively scans a directory for C source files, calculates
    embedding extraction metrics, and saves the statistics to a file.
    """
    # Path object prevents unescaped unicode (\uXXXX) syntax errors in path strings
    dir_path = Path(target_directory)

    statistics = []
    total_chars = 0
    total_tokens = 0
    total_chunks = 0
    processed_files = 0

    # Recursively find supported source files
    source_files = list(
        dict.fromkeys(
            file_path
            for extension in supported_extensions()
            for file_path in dir_path.rglob(f"*{extension}")
        )
    )
    shebang_shell_files = [
        file_path
        for file_path in dir_path.rglob("*")
        if file_path.is_file()
        and not file_path.suffix
        and os.access(file_path, os.X_OK)
        and is_shell_file(file_path)
    ]
    source_files.extend(
        file_path for file_path in shebang_shell_files if file_path not in source_files
    )
    worker_count = workers if workers is not None else os.cpu_count() or 1
    if worker_count < 1:
        raise ValueError("workers must be at least 1")
    worker_count = min(worker_count, len(source_files) or 1)

    if not source_files:
        extensions = ", ".join(sorted(supported_extensions()))
        print(f"No supported source files ({extensions}) found in {dir_path}")
        return

    print(f"Processing {len(source_files)} files with {worker_count} worker processes.")
    start_time = time.monotonic()
    results = [None] * len(source_files)

    with ProcessPoolExecutor(
        max_workers=worker_count,
        mp_context=process_pool_context(),
        initializer=init_worker,
    ) as pool:
        futures = {
            pool.submit(process_source_file, file_path): index
            for index, file_path in enumerate(source_files)
        }

        for future in as_completed(futures):
            index = futures[future]
            row, file_chunk_count, file_char_count, file_token_count, error = (
                future.result()
            )
            processed_files += 1

            if error:
                # Log failure but continue processing the rest of the kernel tree
                print(error)
            else:
                results[index] = row
                total_chars += file_char_count
                total_tokens += file_token_count
                total_chunks += file_chunk_count

            if should_report_progress(processed_files, len(source_files)):
                print_progress(
                    processed_files,
                    len(source_files),
                    total_chars,
                    total_tokens,
                    total_chunks,
                    start_time,
                )

    statistics.extend(row for row in results if row is not None)

    # Append absolute totals to the end of the dataset
    statistics.append(
        ["TOTAL", total_chunks, total_chars, total_tokens, format_cost(total_tokens)]
    )

    # Write output to CSV
    with open(output_csv_path, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(
            ["File Path", "Extracted Chunks", "Characters", "Tokens", "Cost (USD)"]
        )
        writer.writerows(statistics)

    print(f"Processing complete. Processed {len(source_files)} files.")
    print(f"Used {worker_count} worker processes.")
    print(f"Estimated total cost: {format_cost(total_tokens)}")
    print(f"Statistics saved to {output_csv_path}")


if __name__ == "__main__":
    TARGET_FOLDER = r"linux-7.0.2"
    OUTPUT_FILE = r"kernel_embedding_statistics.csv"

    if Path(TARGET_FOLDER).exists():
        process_kernel_directory(TARGET_FOLDER, OUTPUT_FILE)
    else:
        print(f"Directory not found: {TARGET_FOLDER}")
