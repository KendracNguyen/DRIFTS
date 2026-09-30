#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <string>
typedef struct { int dummy; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED {0}
#define portENTER_CRITICAL(m) ((void)(m))
#define portEXIT_CRITICAL(m)  ((void)(m))
class Print { public:
  virtual size_t write(uint8_t) = 0;
  virtual size_t write(const uint8_t*, size_t) = 0;
};
class Stream : public Print { public:
  virtual int available() = 0; virtual int read() = 0;
  virtual int peek() = 0; virtual void flush() = 0;
};
