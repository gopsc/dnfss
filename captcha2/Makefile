CXX      := g++
CXXFLAGS := -std=c++20 -Wall -Wextra -O2
INCS     := -I./inc -I/usr/include/freetype2
LIBS     := -lus_msg -lus_logc -lboost_json -lboost_program_options -lfreetype -lpthread -lm

TARGET   := captcha2.bin
SRC      := src/main.cpp

.PHONY: all clean install

all: $(TARGET)

$(TARGET): $(SRC)
	$(CXX) $(CXXFLAGS) $(INCS) $^ $(LIBS) -o $@

install: $(TARGET)
	cp $(TARGET) /usr/local/bin/

clean:
	rm -f $(TARGET)
