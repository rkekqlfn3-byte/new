import json
import pathlib
import sys

from engine.macro_runner import MacroRunner, MacroTimeoutError


output_path = pathlib.Path(sys.argv[1])
try:
    MacroRunner(timeout=0.2).run("import time\ntime.sleep(5)")
except MacroTimeoutError as error:
    output_path.write_text(
        json.dumps({"error_type": "timeout", "message": str(error)}, ensure_ascii=False),
        encoding="utf-8",
    )
else:
    raise RuntimeError("시간 초과가 발생하지 않았습니다.")
