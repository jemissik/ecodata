call "%PREFIX%\Scripts\activate.bat"

for /f %%i in ('python -c "import pymovebank; print(pymovebank.__path__[0])"') do set pmv_path=%%i

echo|set /p="
call %PREFIX%\Scripts\activate.bat && python -m pymovebank.app
" > "${PREFIX}"/app.bat
