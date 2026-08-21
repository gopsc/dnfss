# robot_agent — 基于 libhttp 的 DeepSeek 视觉机器人 agent 骨架
CXX := g++
CPPFLAGS := --std=c++20 -O2 -Wall -Wextra

LIBHTTP_DIR := ../libhttp
INCS := -I $(LIBHTTP_DIR)/inc
LIBS := -L $(LIBHTTP_DIR)/build -lus_httpclient -lboost_json -lssl -lcrypto -lcurl -ljpeg -lpthread
RPATH := -Wl,-rpath,$(LIBHTTP_DIR)/build

TARGET := robot_agent

SRCS := main.cpp agent.cpp subagent.cpp tools.cpp camera.cpp

all: $(TARGET)

$(TARGET): $(SRCS) tools.hpp
	$(CXX) $(CPPFLAGS) $(INCS) $(SRCS) $(LIBS) $(RPATH) -o $@

clean:
	rm -f $(TARGET)

.PHONY: all clean
