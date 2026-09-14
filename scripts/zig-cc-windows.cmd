@echo off
"%~dp0..\third_party\zig\zig-x86_64-windows-0.16.0\zig.exe" cc -target x86_64-windows-gnu %*
