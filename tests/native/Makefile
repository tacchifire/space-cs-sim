# Host build of the firmware codec. No Zephyr, no board - the codec is deliberately free of both
# so it can be unit tested and later fuzzed on the host.
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O1 -g -I../../firmware/common
SRC := ../../firmware/common/cuberange_proto.c test_proto.c

.PHONY: test asan clean
test: test_proto
	./test_proto

# The codec parses attacker-controlled bytes, so it gets sanitised in CI as well as compiled.
asan: $(SRC) ../../firmware/common/cuberange_proto.h
	$(CC) $(CFLAGS) -fsanitize=address,undefined -o test_proto_asan $(SRC)
	./test_proto_asan

test_proto: $(SRC) ../../firmware/common/cuberange_proto.h
	$(CC) $(CFLAGS) -o $@ $(SRC)

clean:
	rm -f test_proto test_proto_asan
