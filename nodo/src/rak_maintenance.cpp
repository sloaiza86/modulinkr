// La imagen se verifica antes de entrar en BOOT; NVS y configuración no se escriben.
#include "rak_maintenance.h"
#include "device_identity.h"
#include <LittleFS.h>
#include <mbedtls/md.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace rak {
namespace {
HardwareSerial* radio = nullptr;
int8_t rx_pin = -1, tx_pin = -1;
bool (*prepare_callback)() = nullptr;
void (*resume_callback)(bool) = nullptr;
char current[48] = {};
bool uploading = false, recovery = false, boot_confirmed = false;
uint32_t last_chunk_ms = 0;
size_t received = 0;
File upload;
constexpr char image_path[] = "/rak-update.bin";
constexpr char partial_path[] = "/rak-update.part";
constexpr char marker_path[] = "/rak-update.state";

void error(const char* reason) {
    char line[192];
    snprintf(line, sizeof(line), "CFG:ERR %s", reason);
    const size_t len = strlen(line);
    line[len] = '\n';
    Serial.write(reinterpret_cast<const uint8_t*>(line), len + 1);
}

bool prepared() { return radio && (!prepare_callback || prepare_callback()); }
void resume(bool updated = false) { if (resume_callback && !recovery) resume_callback(updated); }

void drain() {
    const uint32_t start = millis();
    uint32_t quiet = start;
    while (millis() - start < 200 && millis() - quiet < 20) {
        while (radio->available() && millis() - start < 200) { radio->read(); quiet = millis(); }
        delay(1);
    }
}

bool at(const char* command, const char* expected, uint32_t timeout = 2000, bool paced = false) {
    drain();
    for (const char* p = command; *p; ++p) {
        radio->write(static_cast<uint8_t>(*p));
        if (paced) { radio->flush(); delay(50); }
    }
    radio->print("\r\n"); radio->flush();
    char line[128] = {};
    size_t length = 0;
    bool discard = false;
    const uint32_t start = millis();
    while (millis() - start < timeout) {
        if (!radio->available()) { delay(1); continue; }
        const char c = static_cast<char>(radio->read());
        if (c == '\r') continue;
        if (c == '\n') {
            line[length] = '\0';
            if (!discard) {
                if (strncmp(line, "AT+VER=RUI_", 11) == 0) observeVersion(line + 7);
                if (strcmp(line, expected) == 0) return true;
            }
            length = 0; discard = false;
        } else if (length < sizeof(line) - 1) line[length++] = c;
        else discard = true;
    }
    return false;
}

bool rawProbe() {
    radio->flush(); radio->end();
    if (radio->setRxBufferSize(4096) != 4096) return false;
    radio->begin(115200, SERIAL_8N1, rx_pin, tx_pin);
    for (unsigned attempt = 0; attempt < 3; ++attempt) {
        radio->print("\r\n"); radio->flush(); delay(100);
        if (at("AT", "OK", 1500, attempt > 0)) return true;
    }
    return false;
}

bool markerValid() {
    File marker = LittleFS.open(marker_path, "r");
    if (!marker) return false;
    char saved[96] = {};
    const size_t len = marker.read(reinterpret_cast<uint8_t*>(saved), sizeof(saved) - 1);
    marker.close();
    char uid[13]; deviceid::text(deviceid::own().uid, 6, uid);
    char wanted[32]; snprintf(wanted, sizeof(wanted), "%s ", uid);
    return len > strlen(wanted) && strncmp(saved, wanted, strlen(wanted)) == 0 &&
           classify(saved + strlen(wanted)) == State::UPDATE_REQUIRED;
}

bool markRecovery() {
    char uid[13]; deviceid::text(deviceid::own().uid, 6, uid);
    char saved[96]; snprintf(saved, sizeof(saved), "%s %s", uid, current);
    File marker = LittleFS.open(marker_path, "w");
    if (!marker) return false;
    const size_t len = strlen(saved);
    const bool ok = marker.write(reinterpret_cast<const uint8_t*>(saved), len) == len;
    marker.flush(); marker.close();
    return ok && markerValid();
}

bool verifiedImage(const char* path) {
    File file = LittleFS.open(path, "r");
    if (!file || file.size() != rakpolicy::image_size) { file.close(); return false; }
    uint8_t block[1024];
    mbedtls_md_context_t hash;
    mbedtls_md_init(&hash);
    bool ok = mbedtls_md_setup(&hash, mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), 0) == 0 &&
              mbedtls_md_starts(&hash) == 0;
    size_t done = 0;
    while (ok && done < rakpolicy::image_size) {
        const size_t got = file.read(block, sizeof(block));
        if (!got) { ok = false; break; }
        if (!done) {
            uint32_t stack = 0, entry = 0;
            if (got < 8) { ok = false; break; }
            memcpy(&stack, block, 4); memcpy(&entry, block + 4, 4);
            ok = stack >= 0x20000000 && stack <= 0x20010000 &&
                 entry >= 0x08004000 && entry < 0x08040000 && (entry & 1);
        }
        if (ok) ok = mbedtls_md_update(&hash, block, got) == 0;
        done += got;
    }
    uint8_t got_sha[32] = {};
    if (ok) ok = mbedtls_md_finish(&hash, got_sha) == 0 && memcmp(got_sha, rakpolicy::image_sha, 32) == 0;
    mbedtls_md_free(&hash); file.close();
    return ok;
}

uint16_t crc16(const uint8_t* data, size_t length) {
    uint16_t crc = 0;
    while (length--) {
        crc ^= static_cast<uint16_t>(*data++) << 8;
        for (unsigned bit = 0; bit < 8; ++bit) crc = crc & 0x8000 ? (crc << 1) ^ 0x1021 : crc << 1;
    }
    return crc;
}

bool waitByte(uint8_t wanted, uint32_t timeout) {
    const uint32_t start = millis();
    while (millis() - start < timeout) {
        if (!radio->available()) { delay(1); continue; }
        const int value = radio->read();
        if (value == wanted) return true;
        if (value == 0x18) return false;
    }
    return false;
}

bool packet(uint8_t sequence, const uint8_t* data, size_t length) {
    const uint16_t crc = crc16(data, length);
    const uint8_t head[] = {static_cast<uint8_t>(length == 1024 ? 0x02 : 0x01), sequence,
                            static_cast<uint8_t>(255 - sequence)};
    const uint8_t tail[] = {static_cast<uint8_t>(crc >> 8), static_cast<uint8_t>(crc)};
    for (unsigned retry = 0; retry < 3; ++retry) {
        radio->write(head, sizeof(head)); radio->write(data, length); radio->write(tail, sizeof(tail)); radio->flush();
        if (waitByte(0x06, 3000)) return true;
    }
    return false;
}

bool transfer() {
    radio->print("at+update\r\n"); radio->flush();
    if (!waitByte(0x43, 10000)) return false;
    uint8_t block[1024] = {};
    const size_t name_size = strlen(rakpolicy::image_version);
    snprintf(reinterpret_cast<char*>(block), 128, "%s.bin", rakpolicy::image_version);
    snprintf(reinterpret_cast<char*>(block) + name_size + 5, 128 - name_size - 5, "%u",
             static_cast<unsigned>(rakpolicy::image_size));
    if (!packet(0, block, 128) || !waitByte(0x43, 10000)) return false;
    File file = LittleFS.open(image_path, "r");
    if (!file) return false;
    size_t done = 0;
    uint8_t sequence = 1;
    while (done < rakpolicy::image_size) {
        memset(block, 0x1A, sizeof(block));
        const size_t got = file.read(block, sizeof(block));
        if (!got || !packet(sequence++, block, sizeof(block))) { file.close(); return false; }
        done += got; report("installing", done);
    }
    file.close();
    // El receptor puede pedir un segundo EOT antes de confirmar el cierre.
    bool ended = false;
    for (unsigned retry = 0; retry < 3 && !ended; ++retry) {
        radio->write(static_cast<uint8_t>(0x04)); radio->flush(); ended = waitByte(0x06, 3000);
    }
    if (!ended || !waitByte(0x43, 10000)) return false;
    memset(block, 0, 128);
    return packet(0, block, 128);
}
}

const char* version() { return current; }
bool approved() { return classify(current) == State::APPROVED && !recovery; }
bool busy() { return uploading || recovery; }
void observeVersion(const char* value) {
    current[0] = '\0';
    if (!value || strlen(value) >= sizeof(current)) return;
    for (const char* p = value; *p; ++p)
        if (!((*p >= 'A' && *p <= 'Z') || (*p >= 'a' && *p <= 'z') ||
              (*p >= '0' && *p <= '9') || *p == '.' || *p == '-' || *p == '_')) return;
    strcpy(current, value);
}
void confirmOperationalVersion() {
    if (recovery && classify(current) == State::APPROVED) {
        LittleFS.remove(marker_path);
        recovery = false;
    }
}
void begin(HardwareSerial& uart, int8_t rx, int8_t tx, bool (*prepare)(), void (*resume_fn)(bool)) {
    radio = &uart; rx_pin = rx; tx_pin = tx;
    prepare_callback = prepare; resume_callback = resume_fn;
    recovery = markerValid();
}

void inspect() {
    if (uploading) { report("busy"); return; }
    observeVersion("");
    boot_confirmed = false;
    if (!prepared()) { report("busy"); return; }
    if (rawProbe()) {
        if (at("AT+PRECV=0", "OK", 2000, true) && at("AT+VER=?", "OK", 2000)) {
            if (classify(current) == State::APPROVED) {
                LittleFS.remove(marker_path); recovery = false;
            }
        }
    } else if (markerValid() && at("at+", "AT not support", 2000)) {
        recovery = true;
        boot_confirmed = true;
    }
    report("checked"); resume();
}

void report(const char* phase, size_t done) {
    char line[320];
    const int n = snprintf(line, sizeof(line), "CFG:RAK {\"version\":\"%s\",\"state\":\"%s\",\"approved\":%s,\"recoverable\":%s,\"phase\":\"%s\",\"done\":%u,\"total\":%u,\"target_version\":\"%s\"}\n",
        current, stateName(classify(current)), approved() ? "true" : "false", recovery ? "true" : "false",
        phase, static_cast<unsigned>(done), static_cast<unsigned>(rakpolicy::image_size), rakpolicy::target);
    if (n > 0 && static_cast<size_t>(n) < sizeof(line)) Serial.write(reinterpret_cast<const uint8_t*>(line), n);
}

bool startUpload(size_t length, const uint8_t sha[32]) {
    if (uploading || length != rakpolicy::image_size || memcmp(sha, rakpolicy::image_sha, 32) != 0) {
        error("RAK image identity mismatch or transfer busy"); return false;
    }
    inspect();
    if ((classify(current) != State::UPDATE_REQUIRED && !(recovery && boot_confirmed)) || !prepared()) {
        error("RAK version is not approved for updating; no automatic downgrade"); return false;
    }
    LittleFS.remove(partial_path);
    if (LittleFS.totalBytes() < LittleFS.usedBytes() + rakpolicy::image_size + 8192) {
        error("Insufficient space for RAK image; existing data retained"); resume(); return false;
    }
    upload = LittleFS.open(partial_path, "w");
    if (!upload) { error("RAK image could not be stored"); resume(); return false; }
    uploading = true; received = 0; last_chunk_ms = millis(); report("receiving");
    return true;
}

bool writeChunk(size_t offset, const uint8_t* bytes, size_t length) {
    if (!uploading || offset != received || !length || length > 1024 || received + length > rakpolicy::image_size) {
        error("RAK transfer offset or chunk length invalid"); return false;
    }
    if (upload.write(bytes, length) != length) { error("RAK image write failed"); abort(); return false; }
    received += length; last_chunk_ms = millis(); report("receiving", received);
    return true;
}

void abort() {
    upload.close(); uploading = false;
    LittleFS.remove(partial_path); report("cancelled"); resume();
}
void tick() { if (uploading && millis() - last_chunk_ms >= 30000) abort(); }

bool install() {
    if (!uploading || received != rakpolicy::image_size) { error("RAK image incomplete"); return false; }
    upload.flush(); upload.close();
    if (!verifiedImage(partial_path)) {
        error("RAK image verification failed; module not written");
        LittleFS.remove(partial_path); uploading = false; resume(); return false;
    }
    LittleFS.remove(image_path);
    if (!LittleFS.rename(partial_path, image_path)) {
        error("RAK verified image could not be selected"); uploading = false; resume(); return false;
    }
    if (!prepared()) {
        error("RAK is not ready to update"); uploading = false; resume(); return false;
    }
    if (boot_confirmed) {
        if (!markerValid() || !at("at+", "AT not support", 2000)) {
            error("RAK recovery bootloader not confirmed"); uploading = false; return false;
        }
    } else {
        observeVersion("");
        if (!rawProbe() || !at("AT+VER=?", "OK", 2000) || classify(current) != State::UPDATE_REQUIRED) {
            error("RAK version changed or not confirmed; module not written"); uploading = false; resume(); return false;
        }
    }
    if (!recovery && !markRecovery()) {
        error("RAK recovery identity could not be stored"); uploading = false; resume(); return false;
    }
    recovery = true;
    report("starting");
    if (!at("AT+BOOT", "<BOOT MODE>", 3000) && !at("at+", "AT not support", 2000)) {
        error("RAK bootloader did not respond; image retained for retry"); uploading = false; return false;
    }
    if (!transfer()) {
        radio->write(static_cast<uint8_t>(0x18));
        radio->write(static_cast<uint8_t>(0x18));
        radio->flush();
        error("RAK transfer failed; image and recovery identity retained"); uploading = false; return false;
    }
    delay(2000); observeVersion("");
    if (!at("AT+VER=?", "OK", 3000) || strcmp(current, rakpolicy::image_version) != 0) {
        error("RAK installed version not confirmed; retry required"); uploading = false; return false;
    }
    LittleFS.remove(marker_path); recovery = false; uploading = false;
    report("complete", rakpolicy::image_size); resume(true);
    return true;
}
}
