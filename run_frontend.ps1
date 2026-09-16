$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root "langchain_env\Scripts\python.exe"
$app = Join-Path $root "LCA\frontend_app.py"
$log = Join-Path $root "streamlit.log"

Set-Location $root
& $python -m streamlit run $app --server.headless true --server.port 8501 --server.address localhost --browser.gatherUsageStats false *> $log
