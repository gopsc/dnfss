CXX      := g++
CXXFLAGS := -std=c++20 -Wall -Wextra -O2
INCS     := -I./inc
LIBS     := -lus_msg -lboost_json -lboost_program_options -lpthread

TARGET   := confs2.bin
SRC      := src/main.cpp

.PHONY: all clean install

all: $(TARGET)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) $(INCS) $^ $(LIBS) -o $@

install: $(TARGET)
	cp $(TARGET) /usr/local/bin/

clean:
	rm -f $(TARGET)
