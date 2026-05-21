/* ============================================================ */
/* MOCK API LAYER                                               */
/* ------------------------------------------------------------ */
/* Intercepts window.fetch() for any URL starting with /api/    */
/* whenever window.MOCK_MODE === true, returning realistic      */
/* sample payloads. When MOCK_MODE is false the real fetch is   */
/* delegated to (so the same UI works against the Python        */
/* backend once /Users/admin/Desktop/flight-hacker/ui/server.py */
/* is wired up).                                                */
/* ============================================================ */

(function () {
  'use strict';

  // -- toggle ---------------------------------------------------
  window.MOCK_MODE = (window.MOCK_MODE === undefined) ? true : window.MOCK_MODE;

  // -- sample airport hub list (would normally be served from   */
  //    data/airport_hubs.json by the backend)                   */
  const AIRPORT_HUBS = [
    { iata: 'JFK', city: 'NEW YORK',     country: 'US' },
    { iata: 'EWR', city: 'NEWARK',       country: 'US' },
    { iata: 'LGA', city: 'NEW YORK',     country: 'US' },
    { iata: 'BOS', city: 'BOSTON',       country: 'US' },
    { iata: 'IAD', city: 'WASHINGTON',   country: 'US' },
    { iata: 'DCA', city: 'WASHINGTON',   country: 'US' },
    { iata: 'MIA', city: 'MIAMI',        country: 'US' },
    { iata: 'FLL', city: 'FT LAUDERDALE',country: 'US' },
    { iata: 'ATL', city: 'ATLANTA',      country: 'US' },
    { iata: 'ORD', city: 'CHICAGO',      country: 'US' },
    { iata: 'MDW', city: 'CHICAGO',      country: 'US' },
    { iata: 'DFW', city: 'DALLAS',       country: 'US' },
    { iata: 'IAH', city: 'HOUSTON',      country: 'US' },
    { iata: 'DEN', city: 'DENVER',       country: 'US' },
    { iata: 'SEA', city: 'SEATTLE',      country: 'US' },
    { iata: 'SFO', city: 'SAN FRANCISCO',country: 'US' },
    { iata: 'LAX', city: 'LOS ANGELES',  country: 'US' },
    { iata: 'SAN', city: 'SAN DIEGO',    country: 'US' },
    { iata: 'YYZ', city: 'TORONTO',      country: 'CA' },
    { iata: 'YUL', city: 'MONTREAL',     country: 'CA' },
    { iata: 'YVR', city: 'VANCOUVER',    country: 'CA' },
    { iata: 'MEX', city: 'MEXICO CITY',  country: 'MX' },
    { iata: 'CUN', city: 'CANCUN',       country: 'MX' },
    { iata: 'BOG', city: 'BOGOTA',       country: 'CO' },
    { iata: 'GRU', city: 'SAO PAULO',    country: 'BR' },
    { iata: 'EZE', city: 'BUENOS AIRES', country: 'AR' },
    { iata: 'SCL', city: 'SANTIAGO',     country: 'CL' },
    { iata: 'LHR', city: 'LONDON',       country: 'GB' },
    { iata: 'LGW', city: 'LONDON',       country: 'GB' },
    { iata: 'STN', city: 'LONDON',       country: 'GB' },
    { iata: 'CDG', city: 'PARIS',        country: 'FR' },
    { iata: 'ORY', city: 'PARIS',        country: 'FR' },
    { iata: 'AMS', city: 'AMSTERDAM',    country: 'NL' },
    { iata: 'FRA', city: 'FRANKFURT',    country: 'DE' },
    { iata: 'MUC', city: 'MUNICH',       country: 'DE' },
    { iata: 'ZRH', city: 'ZURICH',       country: 'CH' },
    { iata: 'VIE', city: 'VIENNA',       country: 'AT' },
    { iata: 'MAD', city: 'MADRID',       country: 'ES' },
    { iata: 'BCN', city: 'BARCELONA',    country: 'ES' },
    { iata: 'LIS', city: 'LISBON',       country: 'PT' },
    { iata: 'FCO', city: 'ROME',         country: 'IT' },
    { iata: 'MXP', city: 'MILAN',        country: 'IT' },
    { iata: 'IST', city: 'ISTANBUL',     country: 'TR' },
    { iata: 'DXB', city: 'DUBAI',        country: 'AE' },
    { iata: 'DOH', city: 'DOHA',         country: 'QA' },
    { iata: 'AUH', city: 'ABU DHABI',    country: 'AE' },
    { iata: 'TLV', city: 'TEL AVIV',     country: 'IL' },
    { iata: 'CAI', city: 'CAIRO',        country: 'EG' },
    { iata: 'JNB', city: 'JOHANNESBURG', country: 'ZA' },
    { iata: 'CPT', city: 'CAPE TOWN',    country: 'ZA' },
    { iata: 'NBO', city: 'NAIROBI',      country: 'KE' },
    { iata: 'DEL', city: 'DELHI',        country: 'IN' },
    { iata: 'BOM', city: 'MUMBAI',       country: 'IN' },
    { iata: 'BLR', city: 'BANGALORE',    country: 'IN' },
    { iata: 'BKK', city: 'BANGKOK',      country: 'TH' },
    { iata: 'SIN', city: 'SINGAPORE',    country: 'SG' },
    { iata: 'KUL', city: 'KUALA LUMPUR', country: 'MY' },
    { iata: 'CGK', city: 'JAKARTA',      country: 'ID' },
    { iata: 'HKG', city: 'HONG KONG',    country: 'HK' },
    { iata: 'TPE', city: 'TAIPEI',       country: 'TW' },
    { iata: 'PVG', city: 'SHANGHAI',     country: 'CN' },
    { iata: 'PEK', city: 'BEIJING',      country: 'CN' },
    { iata: 'CAN', city: 'GUANGZHOU',    country: 'CN' },
    { iata: 'ICN', city: 'SEOUL',        country: 'KR' },
    { iata: 'NRT', city: 'TOKYO',        country: 'JP' },
    { iata: 'HND', city: 'TOKYO',        country: 'JP' },
    { iata: 'KIX', city: 'OSAKA',        country: 'JP' },
    { iata: 'SYD', city: 'SYDNEY',       country: 'AU' },
    { iata: 'MEL', city: 'MELBOURNE',    country: 'AU' },
    { iata: 'AKL', city: 'AUCKLAND',     country: 'NZ' }
  ];

  const SWEET_SPOTS = [
    { program: 'ANA',          route: 'US-JP',    cabin: 'J', miles: 75000,  notes: 'ROUND-TRIP. FUEL SURCHARGES.',           status: 'LIVE'   },
    { program: 'ANA',          route: 'US-EU',    cabin: 'J', miles: 88000,  notes: 'ROUND-TRIP. STAR ALLIANCE PARTNERS.',     status: 'LIVE'   },
    { program: 'AEROPLAN',     route: 'US-EU',    cabin: 'J', miles: 60000,  notes: 'ONE-WAY. NO FUEL SURCHARGES ON UA.',      status: 'LIVE'   },
    { program: 'AEROPLAN',     route: 'NA-NA',    cabin: 'J', miles: 25000,  notes: 'SHORT-HAUL. FANTASTIC FOR PE BUMPS.',     status: 'LIVE'   },
    { program: 'TURKISH MS',   route: 'US-EU',    cabin: 'J', miles: 45000,  notes: 'ONE-WAY VIA IST. PHONE-ONLY HOLDS.',       status: 'GRAY'   },
    { program: 'TURKISH MS',   route: 'US-HI',    cabin: 'Y', miles: 7500,   notes: 'UA METAL. INSANE DEAL IF BOOKABLE.',       status: 'GRAY'   },
    { program: 'AVIANCA LFM',  route: 'US-EU',    cabin: 'J', miles: 63000,  notes: 'ROUND-TRIP. RISK OF DEVALUATION.',        status: 'LIVE'   },
    { program: 'AVIANCA LFM',  route: 'INTRA-EU', cabin: 'J', miles: 25000,  notes: 'ROUND-TRIP ON STAR PARTNERS.',            status: 'LIVE'   },
    { program: 'ALASKA MP',    route: 'US-AS',    cabin: 'J', miles: 75000,  notes: 'CX METAL. NO STOPOVERS ANYMORE.',         status: 'LIVE'   },
    { program: 'ALASKA MP',    route: 'US-AS',    cabin: 'F', miles: 110000, notes: 'JAL FIRST. EXTREMELY LIMITED.',           status: 'LIVE'   },
    { program: 'AA AADVANTAGE',route: 'US-EU',    cabin: 'J', miles: 57500,  notes: 'OFF-PEAK WEB-SPECIAL. NO FUEL.',           status: 'LIVE'   },
    { program: 'AA AADVANTAGE',route: 'US-SA',    cabin: 'J', miles: 57000,  notes: 'WEB-SPECIAL. SOUTH AMERICA.',             status: 'LIVE'   },
    { program: 'AA AADVANTAGE',route: 'US-JP',    cabin: 'F', miles: 80000,  notes: 'JL F. UNICORN.',                          status: 'LIVE'   },
    { program: 'BA AVIOS',     route: 'SHORT-NA', cabin: 'Y', miles: 7500,   notes: 'AA-OPERATED SHORTS. CHEAP.',              status: 'LIVE'   },
    { program: 'BA AVIOS',     route: 'US-EU',    cabin: 'J', miles: 75000,  notes: 'HEAVY FUEL SURCHARGES ON BA METAL.',      status: 'LIVE'   },
    { program: 'IBERIA',       route: 'US-EU',    cabin: 'J', miles: 68000,  notes: 'OFF-PEAK. IB METAL ONLY FOR LOW FEES.',   status: 'LIVE'   },
    { program: 'VIRGIN PTS',   route: 'US-JP',    cabin: 'J', miles: 60000,  notes: 'ANA METAL VIA VIRGIN. CALL-ONLY.',        status: 'GRAY'   },
    { program: 'DELTA SM',     route: 'US-EU',    cabin: 'J', miles: 50000,  notes: 'FLASH-SALE TERRITORY.',                   status: 'GRAY'   },
    { program: 'UA MP',        route: 'US-AS',    cabin: 'J', miles: 88000,  notes: 'STAR PARTNERS. EXCURSIONIST FREE.',        status: 'LIVE'   },
    { program: 'AEROMEXICO',   route: 'US-MX',    cabin: 'J', miles: 50000,  notes: 'ROUND-TRIP. OK AVAILABILITY.',            status: 'LIVE'   }
  ];

  const TRANSFER_PARTNERS = {
    'ANA':           [ { card: 'AMEX MR', ratio: '1:1' } ],
    'AEROPLAN':      [ { card: 'AMEX MR', ratio: '1:1' }, { card: 'CHASE UR', ratio: '1:1' }, { card: 'CAP1 VENTURE', ratio: '2:1.5' }, { card: 'BILT', ratio: '1:1' } ],
    'TURKISH MS':    [ { card: 'CITI TY', ratio: '1:1' }, { card: 'CAP1 VENTURE', ratio: '2:1.5' }, { card: 'BILT', ratio: '1:1' } ],
    'AVIANCA LFM':   [ { card: 'AMEX MR', ratio: '1:1' }, { card: 'CITI TY', ratio: '1:1' }, { card: 'CAP1 VENTURE', ratio: '2:1.5' }, { card: 'BILT', ratio: '1:1' } ],
    'ALASKA MP':     [ { card: 'BILT', ratio: '1:1' } ],
    'AA AADVANTAGE': [ { card: 'BILT', ratio: '1:1' } ],
    'BA AVIOS':      [ { card: 'AMEX MR', ratio: '1:1' }, { card: 'CHASE UR', ratio: '1:1' }, { card: 'CITI TY', ratio: '1:1' }, { card: 'BILT', ratio: '1:1' } ],
    'IBERIA':        [ { card: 'AMEX MR', ratio: '1:1' }, { card: 'CHASE UR', ratio: '1:1' }, { card: 'CITI TY', ratio: '1:1' }, { card: 'BILT', ratio: '1:1' } ],
    'VIRGIN PTS':    [ { card: 'AMEX MR', ratio: '1:1' }, { card: 'CHASE UR', ratio: '1:1' }, { card: 'CITI TY', ratio: '1:1' }, { card: 'CAP1 VENTURE', ratio: '2:1.5' }, { card: 'BILT', ratio: '1:1' } ],
    'DELTA SM':      [ { card: 'AMEX MR', ratio: '1:1' } ],
    'UA MP':         [ { card: 'CHASE UR', ratio: '1:1' }, { card: 'BILT', ratio: '1:1' } ],
    'AEROMEXICO':    [ { card: 'AMEX MR', ratio: '1:1.6' }, { card: 'CITI TY', ratio: '1:1' }, { card: 'CAP1 VENTURE', ratio: '2:1.5' } ]
  };

  // -- helpers --------------------------------------------------
  function rand(seed) {
    let x = (seed | 0) || 1;
    return function () {
      x = (x * 1664525 + 1013904223) | 0;
      return ((x >>> 0) % 1000000) / 1000000;
    };
  }
  function pick(rng, arr) { return arr[Math.floor(rng() * arr.length)]; }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  function genRoute(rng, origins, dests) {
    const o = pick(rng, origins);
    const d = pick(rng, dests);
    return { o: o.iata || o, d: d.iata || d };
  }

  function genResults(body) {
    const origins = (body.origins || []).map(c => ({ iata: c }));
    const dests   = (body.destinations || []).map(c => ({ iata: c }));
    if (!origins.length) origins.push({ iata: 'JFK' });
    if (!dests.length)   dests.push({ iata: 'NRT' });

    const carriers   = ['AA','UA','DL','BA','LH','AF','KL','JL','NH','SQ','CX','QR','TK','EK','AC','VS','IB','OZ','KE','LX'];
    const cabins     = body.cabin && body.cabin.length ? body.cabin : ['Y','J'];
    const risks      = ['LEGAL','LEGAL','LEGAL','GRAY','TOS-RISK'];
    const rng        = rand(Date.now() ^ ((body.origins || []).join('').length * 17) ^ ((body.destinations || []).join('').length * 31));
    const out        = [];
    const wantedMode = body.mode || 'both';
    const count      = 14 + Math.floor(rng() * 8);

    for (let i = 0; i < count; i++) {
      const r       = genRoute(rng, origins, dests);
      const carrier = pick(rng, carriers);
      const stops   = Math.min(parseInt(body.max_stops || 2, 10), Math.floor(rng() * 3));
      const dur     = 240 + Math.floor(rng() * (60 * (parseInt(body.max_hours || 36, 10) - 4)));
      const cabin   = pick(rng, cabins);
      const isAward = wantedMode === 'award' ? true
                    : wantedMode === 'cash'  ? false
                    : rng() > 0.5;
      const miles   = isAward ? Math.round((30 + Math.floor(rng() * 100)) * 1000) : 0;
      const cpp     = isAward ? (1.2 + rng() * 2.5) : 0;
      const cash    = isAward ? Math.round(40 + rng() * 220)
                              : Math.round(180 + rng() * (cabin === 'J' ? 3200 : cabin === 'F' ? 6000 : 700));
      const depHr   = Math.floor(rng() * 24);
      const depMin  = Math.floor(rng() * 4) * 15;
      const dep     = (body.depart_from || '2026-06-15') + ' ' + pad2(depHr) + ':' + pad2(depMin);
      const risk    = pick(rng, risks);

      const total   = isAward ? cash + Math.round(miles * cpp / 100) : cash;

      const segs = [];
      let cur = r.o;
      const stopHubs = ['LHR','CDG','FRA','DXB','DOH','IST','HKG','ICN','SIN'];
      for (let s = 0; s <= stops; s++) {
        const next = s === stops ? r.d : pick(rng, stopHubs);
        segs.push({
          from: cur, to: next,
          carrier: carrier,
          flight: carrier + (100 + Math.floor(rng() * 899)),
          dep_local: pad2(depHr + s * 4) + ':' + pad2(depMin),
          arr_local: pad2((depHr + s * 4 + 6) % 24) + ':' + pad2(depMin),
          aircraft: pick(rng, ['B777','B787','A350','A380','B737','A320','A321','B767']),
          duration_min: 240 + Math.floor(rng() * 360)
        });
        cur = next;
      }

      out.push({
        rank: 0,
        route: r.o + '-' + r.d,
        carrier: carrier,
        depart: dep,
        duration_min: dur,
        stops: stops,
        cabin: cabin,
        cash_usd: cash,
        miles: miles,
        miles_cpp_cents: Number(cpp.toFixed(2)),
        miles_value_usd: Math.round(miles * cpp / 100),
        total_usd: total,
        risk: risk,
        is_award: isAward,
        segments: segs,
        baggage: cabin === 'J' || cabin === 'F'
          ? '2 x 32KG CHECKED INCL. 2 x 23KG CARRY-ON.'
          : '1 x 23KG CHECKED INCL. 1 x 7KG CARRY-ON.',
        fare_class: pick(rng, ['Y','B','M','H','Q','V','W','S','K','L','J','C','D','I','P','F','A']),
        booking_instructions: isAward
          ? 'CALL ' + ['ANA US 1-800','UNITED 1-800','TURKISH +90','AEROPLAN 1-833'][Math.floor(rng() * 4)] + '. HOLD 24-72H DEPENDING ON PROGRAM.'
          : 'BOOK DIRECT ON ' + carrier + '.COM. INCOGNITO + VPN. CLEAR COOKIES.'
      });
    }

    out.sort(function (a, b) { return a.total_usd - b.total_usd; });
    for (let i = 0; i < out.length; i++) out[i].rank = i + 1;
    return out;
  }

  function genMistakes() {
    const seeds = [
      { route: 'JFK-AUH', price: 287, cabin: 'J', source: 'Secret Flying', carrier: 'EY', note: 'ETIHAD BIZ ERROR. POSTED MINUTES AGO. BOOK DIRECT.' },
      { route: 'LAX-NRT', price: 412, cabin: 'J', source: 'VFTW',           carrier: 'JL', note: 'JAL BUSINESS SHOPPING CART GLITCH.' },
      { route: 'BOS-LHR', price: 198, cabin: 'PE',source: 'OMAAT',          carrier: 'BA', note: 'POSSIBLE PUBLISHED FARE - LOW RISK.' },
      { route: 'MIA-SCL', price: 99,  cabin: 'Y', source: 'Reddit r/awardtravel', carrier: 'LA', note: 'LATAM US RT. CONFIRMED BOOKING.' },
      { route: 'SEA-ICN', price: 524, cabin: 'J', source: 'Secret Flying',   carrier: 'OZ', note: 'ASIANA BIZ. PARTNER GDS GLITCH.' },
      { route: 'IAD-DOH', price: 312, cabin: 'J', source: 'VFTW',           carrier: 'QR', note: 'QATAR QSUITES MISFILED. ACT FAST.' },
      { route: 'ORD-FRA', price: 184, cabin: 'Y', source: 'OMAAT',          carrier: 'LH', note: 'PUBLISHED PROMO. ZERO RISK.' },
      { route: 'EWR-CAI', price: 432, cabin: 'J', source: 'Reddit r/awardtravel', carrier: 'MS', note: 'EGYPTAIR BIZ. POINTING TO CALL.' },
      { route: 'DFW-AKL', price: 612, cabin: 'J', source: 'Secret Flying',   carrier: 'AA', note: 'AA J-CLASS PHANTOM FARE.' },
      { route: 'YYZ-DEL', price: 388, cabin: 'PE',source: 'VFTW',           carrier: 'AC', note: 'AIR CANADA PE TO DELHI.' }
    ];
    const now = Date.now();
    return seeds.map(function (s, i) {
      return Object.assign({}, s, {
        id: 'mst_' + i,
        posted_at: new Date(now - Math.floor(Math.random() * 36) * 3600 * 1000).toISOString(),
        risk: s.note.indexOf('PUBLISHED') >= 0 ? 'LEGAL' : 'GRAY'
      });
    });
  }

  function ok(data) {
    return new Response(JSON.stringify(data), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    });
  }

  // -- in-memory store for stateful endpoints -------------------
  const STORE = {
    watches: [
      {
        id: 'w_1',
        origin: 'JFK', destination: 'NRT',
        window_from: '2026-07-01', window_to: '2026-09-30',
        max_usd: 900, cabin: 'J',
        last_check: new Date(Date.now() - 1800 * 1000).toISOString(),
        best_found_usd: 1180,
        alerts: 0,
        paused: false
      },
      {
        id: 'w_2',
        origin: 'LAX', destination: 'CDG',
        window_from: '2026-10-15', window_to: '2026-11-15',
        max_usd: 650, cabin: 'PE',
        last_check: new Date(Date.now() - 7200 * 1000).toISOString(),
        best_found_usd: 712,
        alerts: 3,
        paused: false
      }
    ],
    balances: null,
    settings: null
  };

  // -- the router ----------------------------------------------
  async function route(url, init) {
    const path = (url || '').toString();
    const method = (init && init.method) || 'GET';
    let body = {};
    if (init && init.body) {
      try { body = JSON.parse(init.body); } catch (e) { body = {}; }
    }

    // simulated latency
    await new Promise(function (r) { setTimeout(r, 280 + Math.random() * 420); });

    // --- /api/hubs -------------------------------------------
    if (path.indexOf('/api/hubs') === 0) {
      const q = (path.split('?')[1] || '').toLowerCase();
      const qv = decodeURIComponent((q.split('q=')[1] || '').split('&')[0] || '');
      const hits = !qv ? AIRPORT_HUBS.slice(0, 20)
        : AIRPORT_HUBS.filter(function (h) {
            return h.iata.toLowerCase().indexOf(qv) >= 0
                || h.city.toLowerCase().indexOf(qv) >= 0;
          }).slice(0, 20);
      return ok({ hubs: hits });
    }

    // --- /api/search -----------------------------------------
    if (path.indexOf('/api/search') === 0) {
      const results = genResults(body);
      return ok({
        ok: true,
        query: body,
        count: results.length,
        results: results,
        generated_at: new Date().toISOString()
      });
    }

    // --- /api/mistakes ---------------------------------------
    if (path.indexOf('/api/mistakes') === 0) {
      return ok({ ok: true, mistakes: genMistakes(), generated_at: new Date().toISOString() });
    }

    // --- /api/watchlist --------------------------------------
    if (path.indexOf('/api/watchlist') === 0) {
      if (method === 'POST') {
        const id = 'w_' + (STORE.watches.length + 1) + '_' + Date.now();
        STORE.watches.push(Object.assign(
          {
            id: id, last_check: null, best_found_usd: null,
            alerts: 0, paused: false
          },
          body
        ));
        return ok({ ok: true, id: id });
      }
      if (method === 'DELETE') {
        STORE.watches = STORE.watches.filter(function (w) { return w.id !== body.id; });
        return ok({ ok: true });
      }
      if (method === 'PATCH') {
        STORE.watches = STORE.watches.map(function (w) {
          return w.id === body.id ? Object.assign(w, body) : w;
        });
        return ok({ ok: true });
      }
      return ok({ ok: true, watches: STORE.watches });
    }

    // --- /api/sweet-spots ------------------------------------
    if (path.indexOf('/api/sweet-spots') === 0) {
      return ok({
        ok: true,
        sweet_spots: SWEET_SPOTS,
        transfer_partners: TRANSFER_PARTNERS
      });
    }

    // --- /api/balances ---------------------------------------
    if (path.indexOf('/api/balances') === 0) {
      if (method === 'POST') {
        STORE.balances = body;
        return ok({ ok: true });
      }
      return ok({ ok: true, balances: STORE.balances || {
        currencies: { UR: 0, MR: 0, TY: 0, VENTURE: 0, BILT: 0 },
        airlines:   []
      }});
    }

    // --- /api/settings ---------------------------------------
    if (path.indexOf('/api/settings') === 0) {
      if (method === 'POST') {
        STORE.settings = body;
        return ok({ ok: true });
      }
      return ok({ ok: true, settings: STORE.settings || {
        seats_aero_key: '',
        telegram_webhook: '',
        cpp_source: 'avg',
        cache_ttl: 3600
      }});
    }

    // --- /api/refresh ----------------------------------------
    if (path.indexOf('/api/refresh') === 0) {
      return ok({ ok: true, refreshed_at: new Date().toISOString() });
    }

    // --- fallback --------------------------------------------
    return ok({ ok: false, error: 'mock route not implemented', path: path });
  }

  // -- patch fetch ----------------------------------------------
  const _origFetch = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function (input, init) {
    const u = (typeof input === 'string') ? input : (input && input.url) || '';
    if (window.MOCK_MODE && u.indexOf('/api/') === 0) {
      return route(u, init);
    }
    if (_origFetch) return _origFetch(input, init);
    return Promise.reject(new Error('fetch unavailable'));
  };

  // expose for debug
  window.FH_MOCK = { AIRPORT_HUBS: AIRPORT_HUBS, SWEET_SPOTS: SWEET_SPOTS, STORE: STORE };
})();
