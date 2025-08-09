@echo off
REM Add project root to PYTHONPATH for this session to fix ModuleNotFoundError for 'core'
PATH=e:\newproj\third_party\randomx\win64;%PATH%
set PYTHONPATH=%CD%;%PYTHONPATH%
echo PYTHONPATH set to include project root: %CD%
echo Run your python commands in this terminal after activating venv.
