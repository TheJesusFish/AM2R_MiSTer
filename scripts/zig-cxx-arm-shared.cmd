@echo off
if not defined AM2R_CPPDIR set "AM2R_CPPDIR=%~dp0..\data\build\dmtcp-package\dmtcp\lib"
set "AM2R_LINK=1"
for %%A in (%*) do if "%%~A"=="-c" set "AM2R_LINK=0"
if "%AM2R_LINK%"=="0" (
  "%~dp0..\third_party\zig\zig-x86_64-windows-0.16.0\zig.exe" c++ -target arm-linux-gnueabihf.2.30 -mcpu=cortex_a9 %*
) else (
  "%~dp0..\third_party\zig\zig-x86_64-windows-0.16.0\zig.exe" c++ -target arm-linux-gnueabihf.2.30 -mcpu=cortex_a9 -nostdlib++ %* -L "%AM2R_CPPDIR%" -Wl,-rpath,/media/fat/games/am2r/dmtcp/lib -Wl,--no-as-needed -lc++_am2r -Wl,--as-needed
)
