install:  ; pip install -r requirements.txt
test:     ; python -m pytest -q
eval:     ; python -m mulenet.adversary          # baseline vs hardened vs hardened+graph under evasion -> results/
train:    ; python -m mulenet.train              # -> artifacts/model.joblib
serve:    ; uvicorn mulenet.api:app --port 8000
chain-test:   ; cd chain && npm install && npm test
console:      ; cd console && npm install && npm run dev
aml-adapt:    ; python -m mulenet.aml_adapter $(CSV)   # e.g. make aml-adapt CSV=/path/to/HI-Small_Trans.csv
aml-eval:     ; python -m mulenet.aml_eval             # -> results/aml_validation.md
