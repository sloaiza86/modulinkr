// ModuLinkr, bandeja de muestras no entregadas (implementación)

#include "outbox.h"

#include <LittleFS.h>
#include <cstring>

namespace {

constexpr const char* kPath    = "/outbox.bin";
constexpr const char* kTmpPath = "/outbox.tmp";
// Marca de formato. Si algún día cambia el contenido de una entrada, cambia
// esto y lo viejo se descarta en vez de leerse mal.
constexpr uint8_t kMagic[4] = {'M', 'O', 'B', '1'};

}  // namespace

bool Outbox::push(uint8_t origin, uint16_t seq, const float* values,
                  const uint8_t* st, uint8_t n_values, uint32_t capture_ms,
                  uint32_t ts, bool ts_fixed) {
    if (n_values > kMaxValues) n_values = kMaxValues;

    Entry* slot = nullptr;
    Entry* oldest_used = nullptr;
    for (auto& e : entries_) {
        if (!e.in_use) {
            slot = &e;
            break;
        }
        if (oldest_used == nullptr || e.capture_ms < oldest_used->capture_ms) {
            oldest_used = &e;
        }
    }

    bool had_room = true;
    if (slot == nullptr) {
        slot = oldest_used;  // llena: pisa la más antigua
        had_room = false;
    } else {
        count_++;
    }

    slot->in_use     = true;
    slot->in_flight  = false;  // muestra nueva: aún no va en ningún batch
    slot->origin     = origin;
    slot->seq        = seq;
    slot->capture_ms = capture_ms;
    slot->ts         = ts;
    slot->ts_fixed   = ts_fixed;
    slot->n_values   = n_values;
    for (uint8_t i = 0; i < n_values; ++i) {
        slot->values[i] = values[i];
        slot->st[i]     = (st != nullptr) ? st[i] : 0;
    }
    save();
    return had_room;
}

bool Outbox::remove(uint8_t origin, uint16_t seq) {
    for (auto& e : entries_) {
        if (e.in_use && e.origin == origin && e.seq == seq) {
            e.in_use = false;
            count_--;
            save();
            return true;
        }
    }
    return false;
}

Outbox::Entry* Outbox::oldest(uint8_t origin) {
    Entry* best = nullptr;
    for (auto& e : entries_) {
        if (!e.in_use) continue;
        if (origin != 0 && e.origin != origin) continue;
        if (best == nullptr || e.capture_ms < best->capture_ms) best = &e;
    }
    return best;
}

uint32_t Outbox::oldestCaptureMs() const {
    uint32_t best = 0;
    bool found = false;
    for (const auto& e : entries_) {
        if (!e.in_use) continue;
        if (!found || e.capture_ms < best) {
            best = e.capture_ms;
            found = true;
        }
    }
    return found ? best : 0;
}

void Outbox::drop(Entry& e) {
    if (e.in_use) {
        e.in_use = false;
        count_--;
        save();
    }
}

// ----- Persistencia -----
//
// La bandeja vivía solo en RAM, así que un reinicio se llevaba por delante las
// muestras que estuvieran esperando entrega. No es un caso raro: el 2-ago-2026
// se vio una muestra retenida tres minutos y seis ciclos de reintentos, y ese
// es justo el rato en el que un nodo alimentado por batería o por un panel
// puede quedarse sin tensión. Lo que se pierde ahí no se recupera de ninguna
// otra forma, porque la muestra no llegó a existir en ningún otro sitio.
//
// Se escribe entera y a cada cambio. Suena caro y no lo es: la bandeja solo
// cambia cuando una entrega falla o cuando una muestra sale por fin, que en
// funcionamiento normal es casi nunca. Escribir entera, en vez de por partes,
// evita tener que reconciliar un archivo a medias con la memoria.

void Outbox::save() {
    // Durante la carga no se guarda: si no, restaurar treinta muestras serían
    // treinta escrituras del archivo entero para dejarlo como ya estaba.
    if (cargando_) return;
    File f = LittleFS.open(kTmpPath, "w");
    if (!f) return;
    f.write(kMagic, sizeof(kMagic));
    const uint8_t n = static_cast<uint8_t>(count_);
    f.write(&n, 1);
    for (const auto& e : entries_) {
        if (!e.in_use) continue;
        f.write(&e.origin, 1);
        f.write(reinterpret_cast<const uint8_t*>(&e.seq), sizeof(e.seq));
        f.write(reinterpret_cast<const uint8_t*>(&e.ts), sizeof(e.ts));
        const uint8_t fijado = e.ts_fixed ? 1 : 0;
        f.write(&fijado, 1);
        f.write(&e.n_values, 1);
        f.write(reinterpret_cast<const uint8_t*>(e.values),
                sizeof(float) * e.n_values);
        f.write(e.st, e.n_values);
    }
    f.close();
    LittleFS.rename(kTmpPath, kPath);
}

void Outbox::begin(uint32_t now_ms) {
    File f = LittleFS.open(kPath, "r");
    if (!f) { save(); return; }   // primer arranque: el archivo existe siempre
    cargando_ = true;

    uint8_t cab[5] = {0};
    if (f.read(cab, sizeof(cab)) != sizeof(cab) ||
        memcmp(cab, kMagic, sizeof(kMagic)) != 0) {
        f.close();
        cargando_ = false;
        save();          // formato desconocido: se reescribe vacío
        return;
    }
    const uint8_t n = cab[4];
    for (uint8_t i = 0; i < n && i < kCapacity; ++i) {
        uint8_t origin = 0, fijado = 0, n_values = 0;
        uint16_t seq = 0;
        uint32_t ts = 0;
        if (f.read(&origin, 1) != 1) break;
        if (f.read(reinterpret_cast<uint8_t*>(&seq), sizeof(seq)) != sizeof(seq)) break;
        if (f.read(reinterpret_cast<uint8_t*>(&ts), sizeof(ts)) != sizeof(ts)) break;
        if (f.read(&fijado, 1) != 1) break;
        if (f.read(&n_values, 1) != 1) break;
        if (n_values > kMaxValues) break;
        float valores[kMaxValues] = {};
        uint8_t st[kMaxValues] = {};
        const size_t bytes = sizeof(float) * n_values;
        if (f.read(reinterpret_cast<uint8_t*>(valores), bytes) != static_cast<int>(bytes)) break;
        if (f.read(st, n_values) != static_cast<int>(n_values)) break;
        // El millis() de captura no sobrevive al reinicio y no tiene sentido
        // que lo haga: se reparte por índice para conservar el ORDEN, que es
        // lo único que ese campo decide. La hora real de la muestra va en ts,
        // que sí es la que importa y sí se guarda.
        push(origin, seq, valores, st, n_values, now_ms + i, ts, fijado != 0);
    }
    f.close();
    // Y se vuelve a escribir una sola vez, ya con la memoria montada: deja el
    // archivo exactamente igual que ella aunque la lectura se haya truncado a
    // media entrada.
    cargando_ = false;
    save();
}
