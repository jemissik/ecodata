setlocal enabledelayedexpansion

REM Expand the first matching menu dir under %PREFIX%\pkgs
for /d %%D in ("%PREFIX%\pkgs\ecodata-menu*") do (
    set "menudir=%%D\Menu"
    goto :found
)

:found
if not defined menudir (
    echo Menu directory not found under %PREFIX%\pkgs
    exit /b 1
)

if exist "%menudir%\ecodata-menu-prerelease.json" (
    copy "%menudir%\ecodata-menu-prerelease.json" "%menudir%\ecodata-menu.json"
    echo Renamed prerelease menu file in %menudir%
) else (
    echo Prerelease JSON not found in %menudir%
)

endlocal
exit /b 0
