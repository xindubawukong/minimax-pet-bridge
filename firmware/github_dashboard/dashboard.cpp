// SPDX-License-Identifier: MIT
// GitHub leaderboard for Waveshare ESP32-S3-Touch-LCD-4.3C-BOX.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <time.h>
#include <sys/time.h>
#include "src/lvgl_port/lvgl_port.h"

static constexpr int ROWS = 6;
static constexpr uint32_t BG = 0x0B1018, CARD = 0x121B27, FG = 0xEDF3FA;
static constexpr uint32_t MUTED = 0x8899AD, GREEN = 0x64DBA7, BLUE = 0x79AEFF;
static lv_obj_t *org_label, *window_label, *status_label, *clock_label;
struct DisplayRow { lv_obj_t *name, *value, *detail, *bar; };
static DisplayRow contributor_rows[ROWS], repo_rows[ROWS];
static char serial_line[8192];
static size_t serial_used = 0;
static bool discarding = false, has_data = false;
static uint32_t last_sync_ms = 0, last_status_ms = 0;
static unsigned total_commits = 0, total_repos = 0;
static time_t data_until = 0;

static void set_text(lv_obj_t *obj, const char *text) {
    // LVGL 8 invalidates a label even when set_text receives identical content.
    if (strcmp(lv_label_get_text(obj), text) != 0) lv_label_set_text(obj, text);
}

static lv_obj_t *label(lv_obj_t *parent, int x, int y, int width,
                       const char *text, const lv_font_t *font, uint32_t color) {
    lv_obj_t *obj = lv_label_create(parent);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_width(obj, width);
    lv_label_set_long_mode(obj, LV_LABEL_LONG_DOT);
    set_text(obj, text);
    lv_obj_set_style_text_font(obj, font, 0);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    return obj;
}

static void column(int x, const char *title, const char *subtitle,
                   uint32_t accent, DisplayRow *rows) {
    lv_obj_t *card = lv_obj_create(lv_scr_act());
    lv_obj_set_pos(card, x, 86);
    lv_obj_set_size(card, 376, 350);
    lv_obj_set_style_bg_color(card, lv_color_hex(CARD), 0);
    lv_obj_set_style_border_width(card, 0, 0);
    lv_obj_set_style_radius(card, 14, 0);
    lv_obj_set_style_pad_all(card, 0, 0);
    lv_obj_clear_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    label(card, 16, 13, 340, title, &lv_font_montserrat_20, accent);
    label(card, 16, 39, 340, subtitle, &lv_font_montserrat_12, MUTED);
    for (int i = 0; i < ROWS; ++i) {
        const int y = 65 + i * 46;
        char rank[8]; snprintf(rank, sizeof(rank), "%02d", i + 1);
        label(card, 14, y + 1, 25, rank, &lv_font_montserrat_14, MUTED);
        rows[i].name = label(card, 45, y, 211, "--", &lv_font_montserrat_18, FG);
        rows[i].value = label(card, 260, y - 2, 100, "--", &lv_font_montserrat_20, accent);
        lv_obj_set_style_text_align(rows[i].value, LV_TEXT_ALIGN_RIGHT, 0);
        rows[i].detail = label(card, 45, y + 23, 315, "Waiting for Mac", &lv_font_montserrat_12, MUTED);
        rows[i].bar = lv_bar_create(card);
        lv_obj_set_pos(rows[i].bar, 45, y + 41);
        lv_obj_set_size(rows[i].bar, 315, 2);
        lv_bar_set_range(rows[i].bar, 0, 1000);
        lv_obj_set_style_bg_color(rows[i].bar, lv_color_hex(0x253243), LV_PART_MAIN);
        lv_obj_set_style_bg_color(rows[i].bar, lv_color_hex(accent), LV_PART_INDICATOR);
    }
}

static void build_ui() {
    lv_obj_set_style_bg_color(lv_scr_act(), lv_color_hex(BG), 0);
    org_label = label(lv_scr_act(), 20, 13, 470, "UCRPARLAY", &lv_font_montserrat_28, FG);
    window_label = label(lv_scr_act(), 22, 50, 630,
        "LAST 7 DAYS  /  LINES ADDED + DELETED", &lv_font_montserrat_12, MUTED);
    clock_label = label(lv_scr_act(), 635, 19, 140, "USB DISPLAY", &lv_font_montserrat_16, GREEN);
    lv_obj_set_style_text_align(clock_label, LV_TEXT_ALIGN_RIGHT, 0);
    column(16, "Top contributors", "RANKED BY CHANGED LINES", GREEN, contributor_rows);
    column(408, "Top repositories", "RANKED BY CHANGED LINES", BLUE, repo_rows);
    status_label = label(lv_scr_act(), 22, 450, 756,
        "Connect USB and run run-github.command on your Mac", &lv_font_montserrat_14, MUTED);
}

static void number(uint64_t value, char *out, size_t size) {
    if (value >= 1000000000ULL) snprintf(out, size, "%.1fB", value / 1e9);
    else if (value >= 1000000ULL) snprintf(out, size, "%.1fM", value / 1e6);
    else if (value >= 10000ULL) snprintf(out, size, "%.0fk", value / 1e3);
    else if (value >= 1000ULL) snprintf(out, size, "%.1fk", value / 1e3);
    else snprintf(out, size, "%llu", (unsigned long long)value);
}

static uint32_t crc32(const char *data) {
    uint32_t value = 0xFFFFFFFFU;
    for (; *data; ++data) {
        value ^= static_cast<uint8_t>(*data);
        for (int bit = 0; bit < 8; ++bit)
            value = (value >> 1) ^ ((value & 1) ? 0xEDB88320U : 0);
    }
    return value ^ 0xFFFFFFFFU;
}

static bool valid_rows(JsonArrayConst rows) {
    if (rows.isNull() || rows.size() > ROWS) return false;
    uint64_t previous = UINT64_MAX;
    for (JsonObjectConst row : rows) {
        const char *name = row["name"];
        if (!name || !*name || strlen(name) > 100) return false;
        for (const char *key : {"lines", "additions", "deletions", "commits"})
            if (!row[key].is<uint64_t>() || row[key].as<uint64_t>() > INT64_MAX) return false;
        uint64_t lines = row["lines"], adds = row["additions"], dels = row["deletions"];
        if (adds > INT64_MAX - dels || lines != adds + dels || lines > previous) return false;
        previous = lines;
    }
    return true;
}

static void show_rows(JsonArrayConst data, DisplayRow *rows) {
    uint64_t max_lines = data.size() ? data[0]["lines"].as<uint64_t>() : 0;
    for (int i = 0; i < ROWS; ++i) {
        if (i >= data.size()) {
            set_text(rows[i].name, "--");
            set_text(rows[i].value, "");
            set_text(rows[i].detail, "");
            lv_bar_set_value(rows[i].bar, 0, LV_ANIM_OFF);
            continue;
        }
        JsonObjectConst row = data[i];
        char value[24], adds[24], dels[24], detail[100];
        number(row["lines"].as<uint64_t>(), value, sizeof(value));
        number(row["additions"].as<uint64_t>(), adds, sizeof(adds));
        number(row["deletions"].as<uint64_t>(), dels, sizeof(dels));
        snprintf(detail, sizeof(detail), "+%s  -%s   /   %llu commits", adds, dels,
                 (unsigned long long)row["commits"].as<uint64_t>());
        set_text(rows[i].name, row["name"].as<const char *>());
        set_text(rows[i].value, value);
        set_text(rows[i].detail, detail);
        int ratio = max_lines ? (int)(1000.0 * row["lines"].as<uint64_t>() / max_lines) : 0;
        lv_bar_set_value(rows[i].bar, ratio, LV_ANIM_OFF);
    }
}

static void hello() {
    uint64_t mac = ESP.getEfuseMac();
    Serial.printf("GHD1 HELLO id=%04x%08x rows=6 width=800 height=480\n",
                  (unsigned)(mac >> 32), (unsigned)mac);
}

static void process_line(char *line) {
    if (!strcmp(line, "dashboard.hello")) { hello(); return; }
    if (!strcmp(line, "dashboard.status")) {
        if (!lvgl_port_lock(2000)) { Serial.println("GHD1 ERROR display_busy"); return; }
        Serial.printf("GHD1 STATUS uptime_ms=%lu frames=%lu pixels=%llu\n",
            (unsigned long)millis(), (unsigned long)lvgl_port_rendered_frames(),
            (unsigned long long)lvgl_port_rendered_pixels());
        lvgl_port_unlock();
        return;
    }
    if (!strcmp(line, "dashboard.capture")) {
        if (!lvgl_port_lock(2000)) { Serial.println("GHD1 ERROR display_busy"); return; }
        const void *first = lvgl_port_last_frame();
        if (!first) { lvgl_port_unlock(); Serial.println("GHD1 ERROR no_frame"); return; }
        Serial.println("GHD1 FRAME 800 480 768000");
        for (size_t offset = 0; offset < 768000;) {
            // Pace debug captures so the hardware USB FIFO never overruns.
            size_t count = min((size_t)128, (size_t)768000 - offset);
            size_t sent = Serial.write((const uint8_t *)first + offset, count);
            if (!sent) { lvgl_port_unlock(); return; }
            offset += sent;
            Serial.flush();
            delay(2);
        }
        Serial.println("\nGHD1 FRAME END");
        lvgl_port_unlock();
        return;
    }
    if (strncmp(line, "GHD1 ", 5)) return;
    if (strlen(line) < 16 || line[13] != ' ') {
        Serial.println("GHD1 ERROR frame"); return;
    }
    for (int i = 5; i < 13; ++i)
        if (!isxdigit(line[i])) { Serial.println("GHD1 ERROR crc"); return; }
    uint32_t expected = strtoul(line + 5, nullptr, 16);
    const char *payload = line + 14;
    if (crc32(payload) != expected) { Serial.println("GHD1 ERROR checksum"); return; }
    JsonDocument doc;
    if (deserializeJson(doc, payload, DeserializationOption::NestingLimit(5))) {
        Serial.println("GHD1 ERROR json"); return;
    }
    const char *org = doc["org"];
    if (!org || !*org || strlen(org) > 39 ||
        !doc["since"].is<uint32_t>() || !doc["until"].is<uint32_t>() ||
        !doc["generated_at"].is<uint32_t>() || !doc["commit_count"].is<uint32_t>() ||
        !doc["repo_count"].is<uint32_t>() ||
        doc["until"].as<uint32_t>() < doc["since"].as<uint32_t>() ||
        !valid_rows(doc["contributors"].as<JsonArrayConst>()) ||
        !valid_rows(doc["repositories"].as<JsonArrayConst>())) {
        Serial.println("GHD1 ERROR schema"); return;
    }
    if (!lvgl_port_lock(2000)) { Serial.println("GHD1 ERROR display_busy"); return; }
    set_text(org_label, org);
    show_rows(doc["contributors"].as<JsonArrayConst>(), contributor_rows);
    show_rows(doc["repositories"].as<JsonArrayConst>(), repo_rows);
    time_t start = doc["since"].as<uint32_t>(), end = doc["until"].as<uint32_t>();
    data_until = end;
    struct tm start_tm, end_tm;
    localtime_r(&start, &start_tm); localtime_r(&end, &end_tm);
    char range[120];
    snprintf(range, sizeof(range), "LAST 7 DAYS  /  %02d.%02d - %02d.%02d  /  ADDED + DELETED",
             start_tm.tm_mon + 1, start_tm.tm_mday, end_tm.tm_mon + 1, end_tm.tm_mday);
    set_text(window_label, range);
    total_commits = doc["commit_count"].as<unsigned>();
    total_repos = doc["repo_count"].as<unsigned>();
    struct timeval now = { (time_t)doc["generated_at"].as<uint32_t>(), 0 };
    settimeofday(&now, nullptr);
    last_sync_ms = millis(); has_data = true;
    lvgl_port_unlock();
    Serial.printf("GHD1 ACK %08lx rows=%u/%u\n", (unsigned long)expected,
        (unsigned)doc["contributors"].size(), (unsigned)doc["repositories"].size());
}

void setup() {
    // A complete leaderboard is larger than HWCDC's 256-byte default queue.
    Serial.setRxBufferSize(8192);
    Serial.setTxBufferSize(8192);
    Serial.begin(115200);
    Serial.setTxTimeoutMs(1000);
    // US Pacific local clock, including daylight saving transitions.
    setenv("TZ", "PST8PDT,M3.2.0,M11.1.0", 1); tzset();
    DEV_I2C_Init(); IO_EXTENSION_Init();
    esp_lcd_touch_handle_t touch = touch_gt911_init(DEV_I2C_Get_Bus_Device());
    esp_lcd_panel_handle_t panel = waveshare_esp32_s3_rgb_lcd_init();
    ESP_ERROR_CHECK(lvgl_port_init(panel, touch));
    if (lvgl_port_lock(-1)) { build_ui(); lvgl_port_unlock(); }
    IO_EXTENSION_Pwm_Output(10); waveshare_rgb_lcd_bl_on();
    hello();
}

void loop() {
    int budget = 4096;
    while (budget-- && Serial.available()) {
        char ch = Serial.read();
        if (ch == '\n') {
            if (!discarding) { serial_line[serial_used] = 0; process_line(serial_line); }
            serial_used = 0; discarding = false;
        } else if (ch != '\r' && !discarding) {
            if (serial_used < sizeof(serial_line) - 1) serial_line[serial_used++] = ch;
            else { discarding = true; Serial.println("GHD1 ERROR oversized"); }
        }
    }
    if (millis() - last_status_ms >= 1000 && lvgl_port_lock(20)) {
        last_status_ms = millis();
        if (has_data) {
            char footer[150], clock[24];
            unsigned minutes = (millis() - last_sync_ms) / 60000;
            bool stale = minutes >= 30 || time(nullptr) - data_until >= 1800;
            snprintf(footer, sizeof(footer), "%u commits  /  %u repos  /  USB synced %um ago%s",
                     total_commits, total_repos, minutes, stale ? "  -  DATA MAY BE STALE" : "");
            set_text(status_label, footer);
            time_t now = time(nullptr); struct tm tm; localtime_r(&now, &tm);
            strftime(clock, sizeof(clock), "%H:%M", &tm);
            set_text(clock_label, clock);
        }
        lvgl_port_unlock();
    }
    delay(2);
}
