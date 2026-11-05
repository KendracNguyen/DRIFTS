// ===============================================================
//  line_buffer.h - newline-framed text from a byte stream
//
//  Plain C++ (no Arduino), so test/test_pi_uart.cpp checks it on a
//  laptop. A line longer than the buffer is dropped whole rather than
//  split, so a garbled burst on the wire can never look like a command.
// ===============================================================

#pragma once

#include <stddef.h>
#include <string.h>

template <size_t N>
class LineBuffer {
public:
    // Feed one byte. Returns true when a complete, non-empty line is ready
    // in line() (CR and LF both end a line; surrounding spaces trimmed).
    bool push(char c) {
        if (c == '\r' || c == '\n') {
            if (overflow_) {             // the tail of a too-long line
                overflow_ = false;
                len_ = 0;
                return false;
            }
            while (len_ > 0 && buf_[len_ - 1] == ' ') len_--;
            buf_[len_] = '\0';
            const bool ready = len_ > 0;
            len_ = 0;
            if (ready) {
                start_ = 0;
                while (buf_[start_] == ' ') start_++;
            }
            return ready && buf_[start_] != '\0';
        }
        if (overflow_) return false;
        if (len_ >= N - 1) {             // too long: drop it all, wait for the next line end
            overflow_ = true;
            dropped_++;
            return false;
        }
        buf_[len_++] = c;
        return false;
    }

    const char *line() const { return buf_ + start_; }
    unsigned long dropped() const { return dropped_; }

private:
    char buf_[N] = {0};
    size_t len_ = 0;
    size_t start_ = 0;
    bool overflow_ = false;
    unsigned long dropped_ = 0;
};
