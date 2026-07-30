/**
 * Genera n8n/imap_ingest_workflow.json a partir de shared/parseGpsEmail.js.
 *
 * El nodo Code de n8n no puede hacer `require` de un archivo del disco sin
 * habilitar NODE_FUNCTION_ALLOW_EXTERNAL, así que el parser va embebido dentro
 * del workflow. Para que esa copia no se separe del original, el workflow no se
 * edita a mano: se regenera.
 *
 *   node scripts/build_ingest_workflow.js
 *
 * Correr esto después de cada cambio en el parser, y volver a importar el
 * workflow en n8n.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT = path.join(__dirname, '..');
const PARSER_PATH = path.join(ROOT, 'shared', 'parseGpsEmail.js');
const OUT_PATH = path.join(ROOT, 'n8n', 'imap_ingest_workflow.json');

/**
 * SHA-256 en JavaScript puro.
 *
 * Sustituye a require('crypto') dentro del nodo Code. Tiene que producir
 * exactamente el mismo hash que el backfill: el source_hash es la llave de
 * idempotencia, y dos implementaciones que difieran harían que el mismo correo
 * entre dos veces con hashes distintos. La equivalencia se verifica al final
 * de este script.
 */
const SHA256_SHIM = `
// --- SHA-256 en JS puro (equivalente a crypto.createHash('sha256')) ---
// Generado por scripts/build_ingest_workflow.js — no editar a mano.
const crypto = (() => {
  const K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];

  const rotr = (x, n) => (x >>> n) | (x << (32 - n));

  function sha256Hex(input) {
    // El hash se calcula sobre los bytes UTF-8, igual que
    // crypto.createHash('sha256').update(s, 'utf8').
    const bytes = Array.from(new TextEncoder().encode(input));

    const bitLen = bytes.length * 8;
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) bytes.push(0);

    // Longitud como entero de 64 bits big-endian.
    const hi = Math.floor(bitLen / 0x100000000);
    const lo = bitLen >>> 0;
    bytes.push((hi >>> 24) & 0xff, (hi >>> 16) & 0xff, (hi >>> 8) & 0xff, hi & 0xff);
    bytes.push((lo >>> 24) & 0xff, (lo >>> 16) & 0xff, (lo >>> 8) & 0xff, lo & 0xff);

    let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
    let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;

    const w = new Uint32Array(64);

    for (let i = 0; i < bytes.length; i += 64) {
      for (let t = 0; t < 16; t++) {
        w[t] =
          (bytes[i + t * 4] << 24) |
          (bytes[i + t * 4 + 1] << 16) |
          (bytes[i + t * 4 + 2] << 8) |
          bytes[i + t * 4 + 3];
      }

      for (let t = 16; t < 64; t++) {
        const s0 = rotr(w[t - 15], 7) ^ rotr(w[t - 15], 18) ^ (w[t - 15] >>> 3);
        const s1 = rotr(w[t - 2], 17) ^ rotr(w[t - 2], 19) ^ (w[t - 2] >>> 10);
        w[t] = (w[t - 16] + s0 + w[t - 7] + s1) >>> 0;
      }

      let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;

      for (let t = 0; t < 64; t++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const temp1 = (h + S1 + ch + K[t] + w[t]) >>> 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const temp2 = (S0 + maj) >>> 0;

        h = g; g = f; f = e;
        e = (d + temp1) >>> 0;
        d = c; c = b; b = a;
        a = (temp1 + temp2) >>> 0;
      }

      h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0;
      h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
      h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0;
      h6 = (h6 + g) >>> 0; h7 = (h7 + h) >>> 0;
    }

    return [h0, h1, h2, h3, h4, h5, h6, h7]
      .map((x) => x.toString(16).padStart(8, '0'))
      .join('');
  }

  // Imita la interfaz encadenable que usa el parser.
  return {
    createHash() {
      let data = '';
      const api = {
        update(chunk) { data += chunk; return api; },
        digest() { return sha256Hex(data); },
      };
      return api;
    },
  };
})();
`.trim();

// ---------------------------------------------------------------------------
// Embeber el parser
// ---------------------------------------------------------------------------

const parserSource = fs.readFileSync(PARSER_PATH, 'utf8');

const REQUIRE_LINE = "const crypto = require('crypto');";
if (!parserSource.includes(REQUIRE_LINE)) {
  throw new Error(
    `No se encontró "${REQUIRE_LINE}" en shared/parseGpsEmail.js. ` +
      'El generador quedó desfasado del parser: revísalo antes de continuar.'
  );
}

const embeddedParser = parserSource
  .replace(REQUIRE_LINE, SHA256_SHIM)
  // module.exports no existe en el nodo Code; la función se usa directo.
  .replace(/module\.exports\s*=\s*\{[\s\S]*?\};?\s*$/, '')
  .trimEnd();

const parseNodeCode = `${embeddedParser}

// ---------------------------------------------------------------------------
// Adaptador n8n: toma cada correo del trigger IMAP y lo convierte en la fila
// que espera gps_event. Mismo parser que scripts/backfill_gps_event.js.
// ---------------------------------------------------------------------------

const out = [];

for (const item of $input.all()) {
  const mail = item.json;

  const row = parseGpsEmail({
    subject: mail.subject || '',
    textPlain: mail.textPlain || mail.text || '',
    textHtml: mail.textHtml || mail.html || '',
    mailDate: mail.date || null,
    email_message_id: mail.messageId || (mail.metadata || {})['message-id'] || null,
    imap_uid: mail.uid || null,
  });

  out.push({
    json: {
      ...row,
      // JSONB va como texto: el nodo de Postgres no serializa objetos.
      data_json: JSON.stringify(row.data || {}),
    },
  });
}

return out;`;

// ---------------------------------------------------------------------------
// SQL: idéntico al upsertGpsEvent() del backfill.
// ---------------------------------------------------------------------------

const UPSERT_SQL = `INSERT INTO gps_event (
  unit_code, event_time, type, geofence_name, speed_kmh,
  raw_subject, raw_body, source_hash, geofence_kind, data
)
VALUES ($1, $2::timestamptz, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
ON CONFLICT (source_hash) DO UPDATE SET
  unit_code     = EXCLUDED.unit_code,
  event_time    = EXCLUDED.event_time,
  type          = EXCLUDED.type,
  geofence_name = EXCLUDED.geofence_name,
  speed_kmh     = EXCLUDED.speed_kmh,
  raw_subject   = EXCLUDED.raw_subject,
  raw_body      = EXCLUDED.raw_body,
  geofence_kind = EXCLUDED.geofence_kind,
  data          = EXCLUDED.data
RETURNING id;`;

const QUERY_REPLACEMENT = [
  '{{ $json.unit_code }}',
  '{{ $json.event_time }}',
  '{{ $json.event_type }}',
  '{{ $json.geofence_name }}',
  '{{ $json.speed_kmh }}',
  '{{ $json.raw_subject }}',
  '{{ $json.raw_body }}',
  '{{ $json.source_hash }}',
  '{{ $json.geofence_kind }}',
  '{{ $json.data_json }}',
].join(', ');

const workflow = {
  name: 'GPS: Ingesta IMAP → gps_event',
  nodes: [
    {
      parameters: {
        // "resolved" entrega subject, textPlain, textHtml y metadata ya parseados.
        format: 'resolved',
        // Solo los no leídos; al procesarlos se marcan como leídos, que es lo
        // que evita reprocesar el buzón entero en cada ciclo.
        postProcessAction: 'read',
        options: {},
      },
      id: 'ImapTrigger',
      name: 'Email Trigger (IMAP)',
      type: 'n8n-nodes-base.emailReadImap',
      typeVersion: 2,
      position: [-720, 0],
      credentials: {
        imap: { id: '__RELINK__', name: 'ALERTAS_GPS_IMAP' },
      },
    },
    {
      parameters: { jsCode: parseNodeCode },
      id: 'ParseGpsEmail',
      name: 'Parse GPS Email',
      type: 'n8n-nodes-base.code',
      typeVersion: 2,
      position: [-480, 0],
    },
    {
      parameters: {
        conditions: {
          options: { caseSensitive: true, leftValue: '', typeValidation: 'strict', version: 2 },
          conditions: [
            {
              id: 'parse-ok',
              leftValue: '={{ $json.parse_ok }}',
              rightValue: true,
              operator: { type: 'boolean', operation: 'true', singleValue: true },
            },
          ],
          combinator: 'and',
        },
        options: {},
      },
      id: 'IfParseOk',
      name: 'IF parse_ok',
      type: 'n8n-nodes-base.if',
      typeVersion: 2,
      position: [-240, 0],
    },
    {
      parameters: {
        operation: 'executeQuery',
        query: UPSERT_SQL,
        options: { queryReplacement: `=${QUERY_REPLACEMENT}` },
      },
      id: 'UpsertGpsEvent',
      name: 'Postgres: Upsert gps_event',
      type: 'n8n-nodes-base.postgres',
      typeVersion: 2.4,
      position: [40, -100],
      credentials: {
        postgres: { id: '__RELINK__', name: 'GPS_POSTGRES' },
      },
    },
    {
      parameters: {},
      id: 'ReviewQueue',
      name: 'Sin unidad u hora → revisar',
      type: 'n8n-nodes-base.noOp',
      typeVersion: 1,
      position: [40, 120],
    },
  ],
  connections: {
    'Email Trigger (IMAP)': {
      main: [[{ node: 'Parse GPS Email', type: 'main', index: 0 }]],
    },
    'Parse GPS Email': {
      main: [[{ node: 'IF parse_ok', type: 'main', index: 0 }]],
    },
    'IF parse_ok': {
      main: [
        [{ node: 'Postgres: Upsert gps_event', type: 'main', index: 0 }],
        [{ node: 'Sin unidad u hora → revisar', type: 'main', index: 0 }],
      ],
    },
  },
  settings: { executionOrder: 'v1' },
};

// ---------------------------------------------------------------------------
// Verificación: el parser embebido tiene que dar el mismo source_hash que el
// original. Si difiere, la idempotencia se rompe y el histórico se duplica.
// ---------------------------------------------------------------------------

const { parseGpsEmail: parserOriginal } = require(PARSER_PATH);

// eslint-disable-next-line no-new-func
const parserEmbebido = new Function(`${embeddedParser}\nreturn parseGpsEmail;`)();

const CASOS = [
  {
    subject: 'Descarga de Combustible (UNID T-142)',
    textPlain:
      '14.03.2026 18:22:07 unit near \'CARR. TORREON-SALTILLO KM 32\' with speed 0 km/h.\n' +
      'descarga de combustible de 85,5 l',
    mailDate: '2026-03-14T18:23:10.000Z',
    email_message_id: '<a1@proveedor>',
    imap_uid: 4711,
  },
  {
    subject: 'PERDIDA DE CONEXION 30 MIN (V MOVIL 4)',
    textPlain: '15.03.2026 03:10:00 cerca de \'PATIO TORREON\'',
    mailDate: '2026-03-15T03:11:00.000Z',
    email_message_id: '<b2@proveedor>',
  },
  {
    subject: 'Llenado de Combustible (TRANSIT V88)',
    textPlain:
      '16.03.2026 09:00:00 near \'GASOLINERA PEMEX LERDO\' with speed 3.5 km/h.\n' +
      'Llenado de Combustible 300 l\nOdometro ... value of 812345 km',
    mailDate: '2026-03-16T09:01:00.000Z',
    email_message_id: '<c3@proveedor>',
  },
  {
    // Sin unidad ni hora reconocibles: debe caer en la rama de revisión.
    subject: 'Aviso general del sistema',
    textPlain: 'Mantenimiento programado.',
    mailDate: '2026-03-17T00:00:00.000Z',
  },
];

let fallos = 0;

for (const caso of CASOS) {
  const a = parserOriginal(caso);
  const b = parserEmbebido(caso);

  if (JSON.stringify(a) !== JSON.stringify(b)) {
    console.error(`FALLO: divergencia con "${caso.subject}"`);
    console.error('  original:', JSON.stringify(a).slice(0, 200));
    console.error('  embebido:', JSON.stringify(b).slice(0, 200));
    fallos++;
    continue;
  }

  // Y el hash tiene que coincidir con el de crypto, no solo entre ambos.
  const esperado = crypto.createHash('sha256').update(a.hash_input, 'utf8').digest('hex');
  if (a.source_hash !== esperado) {
    console.error(`FALLO: source_hash distinto de crypto en "${caso.subject}"`);
    fallos++;
  }
}

// Casos extra para el SHA-256 puro: cadenas vacías, acentos, y longitudes
// alrededor de los límites de bloque (55/56/64 bytes), que es donde fallan
// las implementaciones a medias.
const shim = new Function(`${SHA256_SHIM}\nreturn crypto;`)();
const CADENAS = [
  '',
  'a',
  'Torreón, Coah. — descarga de combustible 85,5 l',
  'x'.repeat(55),
  'x'.repeat(56),
  'x'.repeat(63),
  'x'.repeat(64),
  'x'.repeat(65),
  'x'.repeat(1000),
  '🚛|T-142|2026-03-14T18:22:07Z',
];

for (const s of CADENAS) {
  const esperado = crypto.createHash('sha256').update(s, 'utf8').digest('hex');
  const obtenido = shim.createHash('sha256').update(s).digest('hex');
  if (esperado !== obtenido) {
    console.error(`FALLO: SHA-256 distinto para una cadena de ${s.length} caracteres`);
    console.error(`  esperado: ${esperado}\n  obtenido: ${obtenido}`);
    fallos++;
  }
}

if (fallos > 0) {
  console.error(`\n${fallos} verificación(es) fallida(s). No se escribió el workflow.`);
  process.exit(1);
}

fs.writeFileSync(OUT_PATH, `${JSON.stringify(workflow, null, 2)}\n`, 'utf8');

console.log(`OK: ${CASOS.length} casos del parser y ${CADENAS.length} cadenas de SHA-256 coinciden.`);
console.log(`Escrito: ${path.relative(ROOT, OUT_PATH)}`);
