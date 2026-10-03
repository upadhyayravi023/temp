// MouseCornerInjector.cpp — KeyFlow Mouse Corner Injector
// Part of the KeyFlow project. Receives OPTION A/B/C/D over TCP and
// moves the cursor to the corresponding screen corner via Interception.

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include "../Listener/interception.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

constexpr unsigned short MOUSE_OPTION_PORT = 9921;
static InterceptionContext g_context = nullptr;

// Interception uses keyboard slots 1..10 and mouse slots 11..20.
constexpr InterceptionDevice INTERCEPTION_MOUSE_FIRST = 11;
constexpr InterceptionDevice INTERCEPTION_MOUSE_LAST = 20;

struct AbsolutePoint
{
    int32_t x;
    int32_t y;
};

static AbsolutePoint CornerFromOption(char option)
{
    switch (std::toupper(static_cast<unsigned char>(option)))
    {
    case 'A':
        return {0, 0}; // Top-left
    case 'B':
        return {65535, 0}; // Top-right
    case 'C':
        return {65535, 65535}; // Bottom-right
    case 'D':
        return {0, 65535}; // Bottom-left
    default:
        throw std::invalid_argument("Invalid corner option. Expected A, B, C, or D.");
    }
}

static void passThroughMouseEvents()
{
    InterceptionDevice device;
    InterceptionStroke stroke;
    while ((device = interception_wait(g_context)) > 0)
    {
        if (interception_is_mouse(device) &&
            interception_receive(g_context, device, &stroke, 1) > 0)
        {
            interception_send(g_context, device, &stroke, 1);
        }
    }
}

static int InjectAbsolutePosition(int x, int y)
{
    InterceptionMouseStroke stroke{};
    stroke.state = 0;
    stroke.flags = INTERCEPTION_MOUSE_MOVE_ABSOLUTE | INTERCEPTION_MOUSE_VIRTUAL_DESKTOP;
    stroke.x = x;
    stroke.y = y;

    int sentCount = 0;
    // The desktop pointer may be on a slot with no hardware ID, so broadcast
    // the same absolute position to every mouse slot as test_abs.cpp does.
    for (int index = 0; index < INTERCEPTION_MAX_MOUSE; ++index)
    {
        sentCount += interception_send(g_context, INTERCEPTION_MOUSE(index),
            reinterpret_cast<const InterceptionStroke*>(&stroke), 1);
    }

    if (sentCount == 0)
        throw std::runtime_error("Interception could not send the mouse stroke to any mouse slot.");
    return sentCount;
}

static bool SetCursorOnInputDesktop(int x, int y, DWORD& errorCode)
{
    bool moved = false;
    errorCode = ERROR_SUCCESS;

    // Use a fresh thread so switching to the active kiosk desktop is not
    // blocked by windows or hooks owned by the receiver's main thread.
    std::thread cursorThread([&]()
    {
        HDESK inputDesktop = OpenInputDesktop(
            0, FALSE, DESKTOP_READOBJECTS | DESKTOP_WRITEOBJECTS);
        if (!inputDesktop)
        {
            errorCode = GetLastError();
            return;
        }

        if (!SetThreadDesktop(inputDesktop))
        {
            errorCode = GetLastError();
            CloseDesktop(inputDesktop);
            return;
        }

        moved = SetCursorPos(x, y) != FALSE;
        if (!moved)
            errorCode = GetLastError();
        CloseDesktop(inputDesktop);
    });
    cursorThread.join();
    return moved;
}

static int InjectMouseToCorner(char option)
{
    const AbsolutePoint point = CornerFromOption(option);
    const int sentCount = InjectAbsolutePosition(point.x, point.y);

    const int left = GetSystemMetrics(SM_XVIRTUALSCREEN);
    const int top = GetSystemMetrics(SM_YVIRTUALSCREEN);
    const int width = (std::max)(1, GetSystemMetrics(SM_CXVIRTUALSCREEN));
    const int height = (std::max)(1, GetSystemMetrics(SM_CYVIRTUALSCREEN));
    const int cursorX = left + point.x * (width - 1) / 65535;
    const int cursorY = top + point.y * (height - 1) / 65535;
    DWORD errorCode = ERROR_SUCCESS;
    if (!SetCursorOnInputDesktop(cursorX, cursorY, errorCode))
    {
        throw std::runtime_error("Windows could not move the cursor on the active input desktop (error " +
                                 std::to_string(errorCode) + "). Interception accepted " +
                                 std::to_string(sentCount) + " mouse stroke(s).");
    }
    return sentCount;
}

static bool HandleOptionLine(const std::string& line)
{
    const std::string prefix = "OPTION\t";
    if (line.size() != prefix.size() + 1 || line.compare(0, prefix.size(), prefix) != 0)
        return false;

    try
    {
        const int sentCount = InjectMouseToCorner(line.back());
        std::cout << "[mouse] Sent option " << static_cast<char>(std::toupper(
            static_cast<unsigned char>(line.back()))) << " to " << sentCount
                  << " mouse slot(s).\n" << std::flush;
        return true;
    }
    catch (const std::exception& error)
    {
        std::cerr << "[mouse] " << error.what() << "\n" << std::flush;
        return false;
    }
}

int main(int argc, char* argv[])
{
    const bool selfTest = argc == 2 && std::string(argv[1]) == "--self-test";
    if (selfTest)
    {
        g_context = interception_create_context();
        if (!g_context)
        {
            std::cerr << "[mouse] Could not open the Interception driver. Run this program elevated and verify the driver is installed.\n";
            return 1;
        }
        interception_set_filter(g_context, interception_is_mouse,
                                INTERCEPTION_FILTER_MOUSE_BUTTON_5_DOWN);
        try
        {
            const int sentCount = InjectAbsolutePosition(32767, 32767);
            std::cout << "[mouse] Self-test sent center position to " << sentCount
                      << " mouse slot(s).\n";
        }
        catch (const std::exception& error)
        {
            std::cerr << "[mouse] Self-test failed: " << error.what() << "\n";
            interception_destroy_context(g_context);
            return 1;
        }
        interception_destroy_context(g_context);
        return 0;
    }

    WSADATA winsock{};
    if (WSAStartup(MAKEWORD(2, 2), &winsock) != 0)
    {
        std::cerr << "[mouse] Winsock startup failed.\n";
        return 1;
    }

    SOCKET listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listener == INVALID_SOCKET)
    {
        std::cerr << "[mouse] Could not create TCP listener.\n";
        WSACleanup();
        return 1;
    }

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons(MOUSE_OPTION_PORT);

    if (bind(listener, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR ||
        listen(listener, SOMAXCONN) == SOCKET_ERROR)
    {
        std::cerr << "[mouse] Could not listen on 127.0.0.1:" << MOUSE_OPTION_PORT
                  << " (socket error " << WSAGetLastError() << ").\n";
        closesocket(listener);
        WSACleanup();
        return 1;
    }

    g_context = interception_create_context();
    if (!g_context)
    {
        std::cerr << "[mouse] Could not open the Interception driver. Run this program elevated and verify the driver is installed.\n";
        closesocket(listener);
        WSACleanup();
        return 1;
    }
    interception_set_filter(g_context, interception_is_mouse,
                            INTERCEPTION_FILTER_MOUSE_BUTTON_5_DOWN);
    std::thread(passThroughMouseEvents).detach();

    std::cout << "[mouse] Waiting for OPTION A/B/C/D on 127.0.0.1:"
              << MOUSE_OPTION_PORT << "...\n" << std::flush;

    for (;;)
    {
        SOCKET client = accept(listener, nullptr, nullptr);
        if (client == INVALID_SOCKET)
            continue;

        std::string pending;
        char buffer[128];
        bool handled = false;
        for (;;)
        {
            const int received = recv(client, buffer, sizeof(buffer), 0);
            if (received <= 0)
                break;
            pending.append(buffer, received);
            const size_t newline = pending.find('\n');
            if (newline != std::string::npos)
            {
                std::string line = pending.substr(0, newline);
                if (!line.empty() && line.back() == '\r')
                    line.pop_back();
                handled = HandleOptionLine(line);
                break;
            }
            if (pending.size() > 64)
                break;
        }

        const char* response = handled ? "OK\n" : "ERROR\n";
        send(client, response, static_cast<int>(std::strlen(response)), 0);
        shutdown(client, SD_SEND);
        closesocket(client);
    }
}
