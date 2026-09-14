@echo off
"%~dp0..\third_party\zig\zig-x86_64-windows-0.16.0\zig.exe" c++ -target arm-linux-gnueabihf.2.30 -mcpu=cortex_a9 %*
