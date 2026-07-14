import json
import pathlib
import sys

from engine.macro_runner import MacroExecutionError, MacroRunner


output_path = pathlib.Path(sys.argv[1])
try:
    MacroRunner(timeout=3).run("raise RuntimeError('의도한-실패')")
except MacroExecutionError as error:
    output_path.write_text(
        json.dumps({
            "returncode": error.returncode,
            "stdout": error.stdout,
            "stderr": error.stderr,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
else:
    raise RuntimeError("실패 매크로가 성공으로 처리됐습니다.")
