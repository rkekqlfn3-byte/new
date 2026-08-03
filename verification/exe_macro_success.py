import json
import pathlib
import sys

from engine.macro_runner import MacroRunner

output_path = pathlib.Path(sys.argv[1])
result = MacroRunner(timeout=3).run(
    "import sys\nprint('nested-worker:' + sys.argv[1])",
    "한글-인수",
)
output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
