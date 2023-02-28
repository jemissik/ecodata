call "%PREFIX%\Scripts\activate.bat"

for /f %%i in ('python -c "import ecodata; print(ecodata.__path__[0])"') do set eco_path=%%i

echo|set /p="
call %PREFIX%\Scripts\activate.bat && python -m ecodata.app
" > "${PREFIX}"/app.bat
