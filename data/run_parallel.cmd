@echo off
chcp 65001 >nul
cd /d D:\Universidad\Master\TFM\java-cognitive-refactor
set REFACTOR_MODE=batch
set REFACTOR_BATCH_MAX_TOKENS=100000
set REFACTOR_BATCH_RETRIES=2
set REFACTOR_BATCH_TIMEOUT=600
echo ============================================================
echo  Ejecucion PARALELA del bucle de refactorizacion (batch)
echo  Lotes grandes (max 100k tokens de salida) + reintentos
echo  dirigidos de los fallidos. 2 workers. Salida en vivo.
echo  Cierra la ventana para detenerla (se reanuda al relanzar).
echo ============================================================
.venv\Scripts\python.exe run_parallel.py -n 2
echo.
echo ============================================================
echo  Proceso terminado. Presiona una tecla para cerrar.
echo ============================================================
pause >nul