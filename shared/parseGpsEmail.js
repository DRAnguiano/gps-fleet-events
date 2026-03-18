const crypto = require('crypto');

function stripHtml(html) {
  if (!html) return '';
  return String(html)
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<\/div>/gi, '\n')
    .replace(/<\/pre>/gi, '\n')
    .replace(/<pre[^>]*>/gi, '')
    .replace(/<a [^>]*>.*?<\/a>/gi, '')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .trim();
}

function toIsoFromDDMMYYYY_HHMMSS(s) {
  const m = (s || '').match(/(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}:\d{2}:\d{2})/);
  if (!m) return null;
  const [, dd, mm, yyyy, hhmmss] = m;
  return `${yyyy}-${mm}-${dd}T${hhmmss}Z`;
}

function normalize(s) {
  return (s || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim();
}

function parseNum(s) {
  if (s == null) return null;
  const t = String(s).replace(',', '.').trim();
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function detectConnectionType(subject, body, normAll) {
  const s = normalize(subject);
  const all = normAll || normalize(`${subject} ${body}`);

  if (
    s.includes('perdida de conexion') ||
    s.includes('pérdida de conexión') ||
    all.includes('conexion perdida') ||
    all.includes('conexión perdida')
  ) return 'CONNECTION_LOST';

  if (
    s.includes('se restablece conexion') ||
    s.includes('se restablece conexión') ||
    all.includes('conexion restaurada') ||
    all.includes('conexión restaurada') ||
    all.includes('conexion restablecida') ||
    all.includes('conexión restablecida')
  ) return 'CONNECTION_RESTORED';

  return null;
}

function parseConnectionMinutesFromSubject(subject) {
  let m = (subject || '').match(/(\d+)\s*MIN/i);
  if (m) return Number(m[1]);

  m = (subject || '').match(/\b1H\b/i);
  if (m) return 60;

  m = (subject || '').match(/\b(\d+)\s*H\b/i);
  if (m) return Number(m[1]) * 60;

  return null;
}

function pickUnitCode(subject, body) {
  let m;

  subject = subject || '';
  body = body || '';

  m =
    subject.match(/\(UNID\s+([A-Z0-9-]+)\)/i) ||
    body.match(/\bUNID\s+([A-Z0-9-]+)\b/i) ||
    subject.match(/\bUNID\s+([A-Z0-9-]+)\b/i);

  if (m) return m[1].toUpperCase();

  m = subject.match(/\((V\s*MOVIL\s*\d+)\)/i);
  if (m) {
    return m[1].toUpperCase().replace(/\s+/g, ' ').trim();
  }

  m =
    subject.match(/\(TRANSIT\s+([A-Z0-9-]+)\)/i) ||
    body.match(/\bTRANSIT\s+([A-Z0-9-]+)\b/i) ||
    subject.match(/\bTRANSIT\s+([A-Z0-9-]+)\b/i);

  if (m) return m[1].toUpperCase();

  m = subject.match(/\(([A-Z]{2,10}-[A-Z0-9-]+)\)/i);
  if (m) return m[1].toUpperCase();

  m =
    subject.match(/\b(T\d{1,4})\b/i) ||
    subject.match(/\b(V\d{1,4})\b/i);

  if (m) return m[1].toUpperCase();

  return null;
}

function buildSourceHash(hashInput) {
  return crypto
    .createHash('sha256')
    .update(hashInput, 'utf8')
    .digest('hex');
}

function parseGpsEmail(input) {
  const subject = (input.subject || '').toString().trim();
  const textPlain = (input.textPlain || input.text || '').toString();
  const textHtml = (input.textHtml || '').toString();
  const rawContent = textHtml || textPlain || '';
  const body = textPlain || stripHtml(rawContent);

  const mailDate = input.mailDate || input.date || null;
  const email_message_id = input.email_message_id || input.messageId || null;
  const imap_uid = input.imap_uid || input.uid || null;

  const textAll = `${subject} ${body}`;
  const norm = normalize(textAll);

  const unit_code = pickUnitCode(subject, body);

  let event_time = null;
  let m = body.match(/(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}:\d{2})/);
  if (m) {
    event_time = toIsoFromDDMMYYYY_HHMMSS(m[1]);
  } else if (mailDate) {
    event_time = new Date(mailDate).toISOString();
  }

  let speed_kmh = null;
  m =
    body.match(/speed\s+(\d+(?:[.,]\d+)?)\s*km\s*\/?\s*h/i) ||
    body.match(/velocidad\s+de\s+(\d+(?:[.,]\d+)?)\s*km\s*\/?\s*h/i);
  if (m) speed_kmh = parseNum(m[1]);

  let address_text = null;
  m =
    body.match(/near\s+'([^']+)'/i) ||
    body.match(/cerca de\s+'([^']+)'/i);
  if (m) address_text = m[1].trim();

  if (!address_text) {
    m =
      body.match(/near\s+(.+?)(?:\n|$)/i) ||
      body.match(/cerca de\s+(.+?)(?:\n|$)/i);
    if (m) address_text = m[1].trim().replace(/\.$/, '');
  }

  let fuel_level_liters = null;
  let odometer_km = null;

  m = body.match(/Total\s+de\s+Combustible.*value\s+of\s+(\d+(?:[.,]\d+)?)\s*l\b/i);
  if (m) fuel_level_liters = parseNum(m[1]);

  m = body.match(/Odometro.*value\s+of\s+(\d+(?:[.,]\d+)?)\s*km\b/i);
  if (m) odometer_km = parseNum(m[1]);

  let event_type = 'OTHER';

  const connType = detectConnectionType(subject, body, norm);
  if (connType) {
    event_type = connType;
  } else {
    if (subject.toLowerCase().includes('sensor de combustible') && fuel_level_liters != null) {
      event_type = 'FUEL_LEVEL';
    } else if (subject.toLowerCase().includes('mileage sensor') && odometer_km != null) {
      event_type = 'MILEAGE';
    } else {
      const isDrain = norm.includes('descarga de combustible');
      const isFill  = norm.includes('llenado de combustible');
      const isIdle  = norm.includes('inactividad') || norm.includes('detenido');
      const isCasetaEnter =
        norm.includes('cruce de caseta') ||
        norm.includes('entro en caseta') ||
        norm.includes('entró en caseta');

      if (isDrain) event_type = 'FUEL_DRAIN';
      else if (isFill) event_type = 'FUEL_FILL';
      else if (isCasetaEnter) event_type = 'CASETA_ENTER';
      else if (isIdle) event_type = 'IDLE';
    }
  }

  let fuel_liters = null;
  m = textAll.match(/Llenado\s+de\s+Combustible\s+(\d+(?:[.,]\d+)?)\s*l\b/i);
  if (m) fuel_liters = parseNum(m[1]);

  if (fuel_liters == null) {
    m = textAll.match(/descarga\s+de\s+combustible\s+de\s+(\d+(?:[.,]\d+)?)\s*l\b/i);
    if (m) fuel_liters = parseNum(m[1]);
  }

  let geofence_name = address_text || null;
  let geofence_kind = 'OTHER';
  const g = normalize(geofence_name || '');

  if (g.includes('caseta')) geofence_kind = 'CASETA';
  else if (g.includes('base') || g.includes('patio')) geofence_kind = 'BASE';
  else if (g.includes('planta') || g.includes('cedis')) geofence_kind = 'PLANTA';
  else if (g.includes('gasolinera') || g.includes('pemex') || g.includes('bp') || g.includes('shell')) {
    geofence_kind = 'GASOLINERA';
  }

  let connection_minutes = null;
  let connection_event = null;

  if (event_type === 'CONNECTION_LOST') {
    connection_event = 'LOST';
    connection_minutes = parseConnectionMinutesFromSubject(subject);
  } else if (event_type === 'CONNECTION_RESTORED') {
    connection_event = 'RESTORED';
    connection_minutes = parseConnectionMinutesFromSubject(subject);
  }

  const hash_input = [
    unit_code || '',
    event_time || '',
    event_type || '',
    geofence_name || '',
    speed_kmh ?? '',
    fuel_liters ?? '',
    fuel_level_liters ?? '',
    odometer_km ?? '',
    connection_event ?? '',
    connection_minutes ?? '',
    subject || '',
    email_message_id || '',
    imap_uid || ''
  ].join('|');

  const source_hash = buildSourceHash(hash_input);

  return {
    unit_code,
    event_time,
    event_type,
    geofence_name,
    geofence_kind,
    speed_kmh,
    fuel_liters,
    raw_subject: subject || null,
    raw_body: body || null,
    hash_input,
    source_hash,
    parse_ok: Boolean(event_time && unit_code),
    data: {
      address_text,
      geofence_kind,
      fuel_level_liters,
      odometer_km,
      parsed_speed_kmh: speed_kmh,
      connection_event,
      connection_minutes,
      fuel_liters,
      email_message_id,
      imap_uid,
      email_date: mailDate ? new Date(mailDate).toISOString() : null
    }
  };
}

module.exports = {
  parseGpsEmail
};
