// SPDX-License-Identifier: GPL-3.0-or-later
// Minimal Windows shared-mode loopback recorder used for native AM2R audio QA.

#define COBJMACROS
#include <windows.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

static void put_u32(FILE *file, uint32_t value)
{
    unsigned char bytes[4] = {
        (unsigned char)value, (unsigned char)(value >> 8),
        (unsigned char)(value >> 16), (unsigned char)(value >> 24),
    };
    fwrite(bytes, 1, sizeof(bytes), file);
}

static bool write_header(FILE *file, const WAVEFORMATEX *format,
                         uint32_t dataBytes)
{
    uint32_t formatBytes = sizeof(WAVEFORMATEX) + format->cbSize;
    uint32_t formatPad = formatBytes & 1u;
    uint32_t riffBytes = 4u + 8u + formatBytes + formatPad + 8u + dataBytes;
    if (fseek(file, 0, SEEK_SET) != 0) return false;
    fwrite("RIFF", 1, 4, file); put_u32(file, riffBytes);
    fwrite("WAVEfmt ", 1, 8, file); put_u32(file, formatBytes);
    fwrite(format, 1, formatBytes, file);
    if (formatPad) fputc(0, file);
    fwrite("data", 1, 4, file); put_u32(file, dataBytes);
    return !ferror(file);
}

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: %s output.wav seconds\n", argv[0]);
        return 2;
    }
    char *end = NULL;
    double seconds = strtod(argv[2], &end);
    if (!end || *end || seconds <= 0.0 || seconds > 600.0) return 2;

    HRESULT result = CoInitializeEx(NULL, COINIT_MULTITHREADED);
    if (FAILED(result)) return 3;
    IMMDeviceEnumerator *enumerator = NULL;
    IMMDevice *device = NULL;
    IAudioClient *client = NULL;
    IAudioCaptureClient *capture = NULL;
    WAVEFORMATEX *format = NULL;
    FILE *file = NULL;
    uint64_t bytesWritten = 0;
    ULONGLONG deadline = 0;
    int status = 4;

    result = CoCreateInstance(__uuidof(MMDeviceEnumerator), NULL,
                              CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                              (void **)&enumerator);
    if (FAILED(result)) goto done;
    result = enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device);
    if (FAILED(result)) goto done;
    result = device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, NULL,
                              (void **)&client);
    if (FAILED(result)) goto done;
    result = client->GetMixFormat(&format);
    if (FAILED(result)) goto done;
    result = client->Initialize(AUDCLNT_SHAREMODE_SHARED,
                                AUDCLNT_STREAMFLAGS_LOOPBACK,
                                0, 0, format, NULL);
    if (FAILED(result)) goto done;
    result = client->GetService(__uuidof(IAudioCaptureClient),
                                (void **)&capture);
    if (FAILED(result)) goto done;

    file = fopen(argv[1], "wb+");
    if (!file) goto done;
    if (!write_header(file, format, 0)) goto done;
    result = client->Start();
    if (FAILED(result)) goto done;

    deadline = GetTickCount64() + (ULONGLONG)(seconds * 1000.0);
    while (GetTickCount64() < deadline) {
        Sleep(5);
        UINT32 available = 0;
        while (SUCCEEDED(capture->GetNextPacketSize(&available)) && available) {
            BYTE *data = NULL;
            UINT32 frames = 0;
            DWORD flags = 0;
            result = capture->GetBuffer(&data, &frames, &flags, NULL, NULL);
            if (FAILED(result)) goto stopped;
            uint32_t packetBytes = frames * format->nBlockAlign;
            if (bytesWritten + packetBytes > UINT32_MAX) goto stopped;
            if (flags & AUDCLNT_BUFFERFLAGS_SILENT) {
                BYTE zeros[4096] = {};
                uint32_t remaining = packetBytes;
                while (remaining) {
                    uint32_t chunk = remaining < sizeof(zeros) ? remaining : sizeof(zeros);
                    if (fwrite(zeros, 1, chunk, file) != chunk) goto stopped;
                    remaining -= chunk;
                }
            } else if (fwrite(data, 1, packetBytes, file) != packetBytes) {
                capture->ReleaseBuffer(frames);
                goto stopped;
            }
            bytesWritten += packetBytes;
            capture->ReleaseBuffer(frames);
            available = 0;
        }
    }
    status = 0;

stopped:
    client->Stop();
    if (!write_header(file, format, (uint32_t)bytesWritten)) status = 5;
    fflush(file);
    fprintf(stderr, "captured_bytes=%llu rate=%lu channels=%u bits=%u tag=0x%04x\n",
            (unsigned long long)bytesWritten, (unsigned long)format->nSamplesPerSec,
            format->nChannels, format->wBitsPerSample, format->wFormatTag);

done:
    if (file) fclose(file);
    if (format) CoTaskMemFree(format);
    if (capture) capture->Release();
    if (client) client->Release();
    if (device) device->Release();
    if (enumerator) enumerator->Release();
    CoUninitialize();
    if (status != 0) fprintf(stderr, "wasapi_capture_failed status=%d hr=0x%08lx\n", status, (unsigned long)result);
    return status;
}
