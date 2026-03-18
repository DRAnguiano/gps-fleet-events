require('dotenv').config();

const { ImapFlow } = require('imapflow');
const { simpleParser } = require('mailparser');
const { Pool } = require('pg');
const { parseGpsEmail } = require('../shared/parseGpsEmail');

const BATCH_SIZE = Number(process.env.BATCH_SIZE || 5);

const pool = new Pool({
  host: process.env.PGHOST,
  port: Number(process.env.PGPORT || 5432),
  database: process.env.PGDATABASE,
  user: process.env.PGUSER,
  password: process.env.PGPASSWORD,
});

const imap = new ImapFlow({
  host: process.env.IMAP_HOST,
  port: Number(process.env.IMAP_PORT || 993),
  secure: String(process.env.IMAP_SECURE || 'true') === 'true',
  auth: {
    user: process.env.IMAP_USER,
    pass: process.env.IMAP_PASS,
  },
  logger: false,
  connectionTimeout: 120000,
  greetingTimeout: 30000,
  socketTimeout: 900000,
  disableAutoIdle: true,
});

imap.on('error', (err) => {
  console.error('IMAP client error:', err.message);
});

imap.on('close', () => {
  console.log('IMAP connection closed.');
});

async function upsertGpsEvent(client, row) {
  const sql = `
    INSERT INTO gps_event (
      unit_code,
      event_time,
      type,
      geofence_name,
      speed_kmh,
      raw_subject,
      raw_body,
      source_hash,
      geofence_kind,
      data
    )
    VALUES (
      $1, $2::timestamptz, $3, $4, $5, $6, $7, $8, $9, $10::jsonb
    )
    ON CONFLICT (source_hash) DO UPDATE SET
      unit_code      = EXCLUDED.unit_code,
      event_time     = EXCLUDED.event_time,
      type           = EXCLUDED.type,
      geofence_name  = EXCLUDED.geofence_name,
      speed_kmh      = EXCLUDED.speed_kmh,
      raw_subject    = EXCLUDED.raw_subject,
      raw_body       = EXCLUDED.raw_body,
      geofence_kind  = EXCLUDED.geofence_kind,
      data           = EXCLUDED.data
  `;

  const values = [
    row.unit_code,
    row.event_time,
    row.event_type,
    row.geofence_name,
    row.speed_kmh,
    row.raw_subject,
    row.raw_body,
    row.source_hash,
    row.geofence_kind,
    JSON.stringify(row.data || {}),
  ];

  await client.query(sql, values);
}

async function main() {
  const db = await pool.connect();
  let lock = null;
  let okCount = 0;

  try {
    console.log('1) Conectando a IMAP...');
    await imap.connect();
    console.log('2) IMAP conectado.');

    const mailboxName = process.env.IMAP_MAILBOX || 'HISTORICAL';
    console.log(`3) Abriendo mailbox: ${mailboxName}`);
    lock = await imap.getMailboxLock(mailboxName);
    console.log(`4) Mailbox bloqueado: ${mailboxName}`);

    console.log('5) Buscando correos no leídos...');
    const unseenUids = await imap.search({ seen: false });
    console.log(`6) Encontrados no leídos: ${unseenUids.length}`);

    if (!unseenUids.length) {
      console.log(`No hay correos no leídos en ${mailboxName}.`);
      return 0;
    }

    const batchUids = unseenUids.slice(0, BATCH_SIZE);
    console.log(`7) Lote seleccionado: ${batchUids.join(', ')}`);

    console.log('8) Trayendo lote completo con fetchAll...');
    const messages = await imap.fetchAll(
      batchUids,
      { uid: true, source: true },
      { uid: true }
    );
    console.log(`9) Mensajes recibidos: ${messages.length}`);

    const processedUids = [];

    for (const msg of messages) {
      try {
        console.log(`10) Parseando MIME UID ${msg.uid}...`);
        const parsedMail = await simpleParser(msg.source);

        console.log(`11) MIME parseado UID ${msg.uid}`);
        console.log(`12) Subject UID ${msg.uid}: ${parsedMail.subject || '(sin subject)'}`);

        const row = parseGpsEmail({
          subject: parsedMail.subject || '',
          textPlain: parsedMail.text || '',
          textHtml: parsedMail.html || '',
          mailDate: parsedMail.date || null,
          email_message_id: parsedMail.messageId || null,
          imap_uid: msg.uid,
        });

        console.log(`13) Evento generado UID ${msg.uid}: ${row.event_type} / ${row.unit_code || 'NULL'}`);

        if (!row.unit_code) {
          throw new Error('unit_code no detectado por parser');
        }

        await upsertGpsEvent(db, row);
        console.log(`14) UPSERT OK UID ${msg.uid}`);

        processedUids.push(msg.uid);
        okCount++;
        console.log(`15) UID listo para marcar como leído: ${msg.uid}`);
      } catch (err) {
        console.error(`Error interno UID ${msg.uid}:`, err.message);
      }
    }

    if (processedUids.length && imap.usable) {
      console.log(`16) Marcando como leídos ${processedUids.length} UID(s)...`);
      await imap.messageFlagsAdd(processedUids, ['\\Seen'], { uid: true });
      console.log('17) Marcado como leído completado.');
    } else {
      console.log('16) No hubo UIDs para marcar como leídos o la conexión no estaba usable.');
    }

    return okCount;
  } finally {
    try {
      if (lock) {
        console.log('18) Liberando mailbox...');
        lock.release();
      }
    } catch (err) {
      console.error('Error liberando mailbox:', err.message);
    }

    try {
      if (imap.usable) {
        console.log('19) Cerrando sesión IMAP con logout...');
        await imap.logout();
      } else {
        console.log('19) Conexión no usable; no se ejecuta logout.');
      }
    } catch (err) {
      console.error('Error cerrando IMAP:', err.message);
      try { imap.close(); } catch {}
    }

    db.release();
    await pool.end();
    console.log('20) Fin del proceso.');
  }
}

main()
  .then((okCount) => {
    if (!okCount || okCount === 0) {
      console.error('Lote sin inserciones exitosas.');
      process.exit(2);
    }
    console.log(`Inserciones exitosas en el lote: ${okCount}`);
    process.exit(0);
  })
  .catch((err) => {
    console.error('Fallo general:', err);
    process.exit(1);
  });
