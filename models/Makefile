CC ?= cc
CFLAGS ?= -O2 -g -Wall -Wextra -std=c11
BUILD := build
BINS := $(BUILD)/test_epd $(BUILD)/test_windows $(BUILD)/replay

all: $(BUILD)/libepd.a $(BINS)

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/epd_core.o: epd_core.c epd_core.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/libepd.a: $(BUILD)/epd_core.o
	ar rcs $@ $^

$(BINS): $(BUILD)/%: tests/%.c tests/wire.h epd_core.h $(BUILD)/libepd.a
	$(CC) $(CFLAGS) -I. $< $(BUILD)/libepd.a -o $@

# A fixture's variant is its name's prefix (uc8279-device-boot-home.log -> uc8279);
# fixtures are gitignored, so CI finds none and the loop skips the unmatched glob.
test: $(BINS)
	$(BUILD)/test_epd
	$(BUILD)/test_windows
	for f in fixtures/*.log; do [ -f "$$f" ] || continue; v=$$(basename $$f | sed -E 's/^([a-z0-9]+)-.*/\1/'); echo "replay $$v $$f"; $(BUILD)/replay $$v $$f || exit 1; done

clean:
	rm -rf $(BUILD)

.PHONY: all test clean
