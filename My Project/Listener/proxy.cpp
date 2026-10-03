/*
 * proxy.cpp — KeyFlow Keyboard Proxy
 * =================================================
 * Part of the KeyFlow project.
 * Physical keyboard → pass-through to OS + mirror to Python agent (TCP)
 * Agent keystrokes  → injected into OS via interception_send (no re-intercept)
 *
 * Build:
 *   MinGW: g++ -std=c++17 -O2 proxy.cpp -o proxy.exe -lws2_32 -L. -linterception
 *   MSVC:  cl.exe /EHsc /O2 proxy.cpp /Fe:proxy.exe /link ws2_32.lib interception.lib
 *
 * Requirements:
 *   - Interception driver installed (install-interception.exe /install + reboot)
 *   - interception.dll in same folder as proxy.exe
 */

#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <iostream>
#include <string>
#include <thread>
#include <atomic>
#include <mutex>
#include "interception.h"

// ─── Config ──────────────────────────────────────────────────────────────────
static const int  PROXY_PORT      = 9920;

// Sentinel stored in InterceptionKeyStroke.information for synthetic strokes.
// Any physical stroke will have information=0; we use 0xCAFEF00D to mark ours.
static const UINT SYNTH_SENTINEL  = 0xCAFEF00D;

// ─── Globals ─────────────────────────────────────────────────────────────────
static InterceptionContext g_ctx     = nullptr;
// Default to slot 1 (INTERCEPTION_KEYBOARD(0)) so injection works even before
// any physical key is pressed.  Will be updated to the real device on first keystroke.
static InterceptionDevice  g_kbd     = INTERCEPTION_KEYBOARD(0);
static std::atomic<bool>   g_run     { true };

static SOCKET     g_sendSock = INVALID_SOCKET;
static std::mutex g_sockMtx;            // guards g_sendSock

// ─── Helpers ─────────────────────────────────────────────────────────────────

static void sendLine(const std::string& line) {
    // Send a newline-terminated message to Python. Thread-safe.
    // Does NOT modify g_sendSock on error — let the recv loop detect disconnect.
    std::lock_guard<std::mutex> lk(g_sockMtx);
    if (g_sendSock == INVALID_SOCKET) return;
    std::string msg = line + "\n";
    const char* buf = msg.c_str();
    int total = (int)msg.size(), sent = 0;
    while (sent < total) {
        int r = ::send(g_sendSock, buf + sent, total - sent, 0);
        if (r <= 0) break;   // disconnected; recv loop will clean up
        sent += r;
    }
}

// VK + scan → printable UTF-8 character (empty if not printable / on key-up)
static std::string vkToUtf8(UINT vk, UINT scanCode) {
    BYTE ks[256] = {};
    ::GetKeyboardState(ks);
    WCHAR wbuf[4] = {};
    int n = ::ToUnicode(vk, scanCode, ks, wbuf, 4, 0);
    if (n != 1) return "";
    char utf8[8] = {};
    ::WideCharToMultiByte(CP_UTF8, 0, wbuf, 1, utf8, sizeof(utf8), nullptr, nullptr);
    std::string s(utf8);
    if (s == "\t" || s == "\n" || s == "\r") return "";   // protect protocol framing
    return s;
}

// Tab-split helper
static bool nextTok(const std::string& line, size_t& pos, std::string& tok) {
    if (pos > line.size()) return false;
    size_t end = line.find('\t', pos);
    if (end == std::string::npos) end = line.size();
    tok = line.substr(pos, end - pos);
    pos = (end < line.size()) ? end + 1 : line.size() + 1;
    return true;
}

// ─── Synthetic key injection via interception_send ───────────────────────────
static void injectKey(unsigned short code, unsigned short state) {
    InterceptionKeyStroke ks = {};
    ks.code        = code;
    ks.state       = state;
    ks.information = SYNTH_SENTINEL;   // mark as synthetic
    interception_send(g_ctx, g_kbd, (InterceptionStroke*)&ks, 1);
}

// ─── Process one line received from Python ───────────────────────────────────
static void processLine(const std::string& line) {
    size_t pos = 0;
    std::string cmd;
    if (!nextTok(line, pos, cmd)) return;

    if (cmd == "KEY") {
        // KEY\t<scancode>\t<state>
        std::string sc, st;
        if (!nextTok(line, pos, sc) || !nextTok(line, pos, st)) return;
        try {
            unsigned short code  = (unsigned short)std::stoul(sc);
            unsigned short state = (unsigned short)std::stoul(st);
            injectKey(code, state);
            std::cout << "[proxy] -> KEY code=" << code
                      << " state=" << state << "\n" << std::flush;
        } catch (...) {}

    }
}

// ─── Handle one connection: read synthetic events until disconnect ────────────
static void handleConnection(SOCKET sock) {
    {
        std::lock_guard<std::mutex> lk(g_sockMtx);
        g_sendSock = sock;
    }
    std::cout << "[proxy] Agent connected. Forwarding keyboard events.\n" << std::flush;

    char raw[4096];
    std::string leftover;

    while (g_run) {
        fd_set rset; FD_ZERO(&rset); FD_SET(sock, &rset);
        timeval tv = { 0, 100000 };     // 100 ms select timeout
        int sel = ::select(0, &rset, nullptr, nullptr, &tv);
        if (sel == 0) continue;
        if (sel < 0)  break;

        int r = ::recv(sock, raw, sizeof(raw) - 1, 0);
        if (r <= 0) break;
        raw[r] = '\0';
        leftover.append(raw, r);

        size_t nlpos;
        while ((nlpos = leftover.find('\n')) != std::string::npos) {
            std::string ln = leftover.substr(0, nlpos);
            leftover.erase(0, nlpos + 1);
            if (!ln.empty() && ln.back() == '\r') ln.pop_back();
            if (!ln.empty()) processLine(ln);
        }
    }

    {
        std::lock_guard<std::mutex> lk(g_sockMtx);
        g_sendSock = INVALID_SOCKET;
    }
    std::cout << "[proxy] Agent disconnected.\n" << std::flush;
}

// ─── Accept loop (runs in its own thread) ────────────────────────────────────
static void agentThread(SOCKET listenSock) {
    while (g_run) {
        std::cout << "[proxy] Waiting for agent on port " << PROXY_PORT << "...\n" << std::flush;
        SOCKET client = ::accept(listenSock, nullptr, nullptr);
        if (client == INVALID_SOCKET) { ::Sleep(200); continue; }
        handleConnection(client);
        ::closesocket(client);
    }
}

// ─── Main ────────────────────────────────────────────────────────────────────
int main() {
    std::cout << "=========================================\n"
              << "  Interception Proxy  (port " << PROXY_PORT << ")\n"
              << "=========================================\n";

    // ── Interception ─────────────────────────────────────────────────────────
    g_ctx = interception_create_context();
    if (!g_ctx) {
        std::cerr << "[proxy] ERROR: Interception context failed.\n"
                  << "        Run: install-interception.exe /install  then reboot.\n";
        return 1;
    }
    interception_set_filter(g_ctx, interception_is_keyboard, INTERCEPTION_FILTER_KEY_ALL);
    std::cout << "[proxy] Interception driver OK. Default inject device: "
              << g_kbd << "\n";

    // ── Winsock ──────────────────────────────────────────────────────────────
    WSADATA wsa;
    if (::WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        std::cerr << "[proxy] WSAStartup failed.\n"; return 1;
    }

    SOCKET listenSock = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listenSock == INVALID_SOCKET) {
        std::cerr << "[proxy] socket() failed.\n"; return 1;
    }
    { int opt = 1; ::setsockopt(listenSock, SOL_SOCKET, SO_REUSEADDR, (char*)&opt, sizeof(opt)); }

    sockaddr_in addr = {};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port        = htons(PROXY_PORT);
    if (::bind(listenSock, (sockaddr*)&addr, sizeof(addr)) == SOCKET_ERROR) {
        std::cerr << "[proxy] bind() failed on port " << PROXY_PORT
                  << " (WSA error " << ::WSAGetLastError() << ")\n"
                  << "        Another process may already be using this port.\n";
        return 1;
    }
    ::listen(listenSock, 1);

    // ── Start accept thread ───────────────────────────────────────────────────
    std::thread agentThr(agentThread, listenSock);
    agentThr.detach();

    // ── Main: physical keyboard interception loop ────────────────────────────
    std::cout << "[proxy] Keyboard interception active. Ctrl+C to exit.\n" << std::flush;

    InterceptionDevice device;
    InterceptionStroke stroke;

    while (interception_receive(g_ctx,
                                device = interception_wait(g_ctx),
                                &stroke, 1) > 0) {
        if (!interception_is_keyboard(device)) continue;


        InterceptionKeyStroke& ks = *(InterceptionKeyStroke*)&stroke;

        // ── Drop synthetic strokes that looped back (sentinel check) ─────────
        if (ks.information == SYNTH_SENTINEL) {
            // This is our own injected stroke re-appearing — just pass to OS, skip Python
            interception_send(g_ctx, device, &stroke, 1);
            continue;
        }

        // ── Physical keystroke: update keyboard device tracker ────────────────
        g_kbd = device;

        // ── 1. Always pass through to OS immediately ─────────────────────────
        interception_send(g_ctx, device, &stroke, 1);

        // ── 2. Resolve VK and character ──────────────────────────────────────
        UINT vk   = ::MapVirtualKeyEx(ks.code, MAPVK_VSC_TO_VK, ::GetKeyboardLayout(0));
        bool down = (ks.state & INTERCEPTION_KEY_UP) == 0;
        std::string ch = down ? vkToUtf8(vk, ks.code) : "";

        // ── 3. Forward to Python ──────────────────────────────────────────────
        // KEY\t<scan>\t<vk>\t<state>\t<char>
        sendLine("KEY\t"
                 + std::to_string(ks.code)  + "\t"
                 + std::to_string(vk)        + "\t"
                 + std::to_string(ks.state)  + "\t"
                 + ch);
    }

    g_run = false;
    ::closesocket(listenSock);
    ::WSACleanup();
    interception_destroy_context(g_ctx);
    return 0;
}
