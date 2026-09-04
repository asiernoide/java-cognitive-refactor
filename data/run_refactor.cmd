@echo off
chcp 65001 >nul
cd /d D:\Universidad\Master\TFM\java-cognitive-refactor
echo ============================================================
echo  Bucle de refactorizacion con LLM (ejecucion completa)
echo  Salida en vivo. Cierra la ventana para detenerla (se reanuda
echo  automaticamente al volver a ejecutar).
echo ============================================================
.venv\Scripts\python.exe refactor_loop.py
echo.
echo ============================================================
echo  Proceso terminado. Presiona una tecla para cerrar.
echo ============================================================
pause >nul