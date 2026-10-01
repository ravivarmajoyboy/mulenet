"""Train the adversarially hardened model and save it for the API.  python -m mulenet.train"""
from pathlib import Path

import joblib

from .adversary import train_hardened


def main() -> None:
    m = train_hardened(use_graph=True)
    Path("artifacts").mkdir(exist_ok=True)
    joblib.dump(m, "artifacts/model.joblib")
    print(f"saved artifacts/model.joblib  use_graph={m.use_graph} "
          f"hold_thr={m.hold_thr:.3f} step_thr={m.step_thr:.3f}")


if __name__ == "__main__":
    main()
