from typing import Any, Dict


def run(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "completed",
        "artifacts": [],
        "summary": "Completed.",
        "result": job.get("payload") or {},
        "logs": None,
    }
