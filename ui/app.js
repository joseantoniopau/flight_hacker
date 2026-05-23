/* ============================================================ */
/* FLIGHT-HACKER UI — APP LOGIC                                 */
/* ------------------------------------------------------------ */
/* Vanilla JS module pattern. Public surface lives under        */
/* window.FH.<section>.<action>. No frameworks, no build step.  */
/* ============================================================ */

(function () {
  'use strict';

  const FH = window.FH = {};

  // ============================================================ //
  // CORE                                                         //
  // ============================================================ //

  FH.core = {
    version: '0.1.0',
    routes: ['search', 'results', 'mistakes', 'watchlist', 'sweet-spots', 'balances', 'settings', 'flex'],

    init: function () {
      document.getElementById('fh-version').textContent = 'v' + FH.core.version;
      // Footer version mirrors the topbar version; build hash is a static
      // placeholder until a real CI write replaces it.
      const fv = document.getElementById('fh-footer-version');
      if (fv) fv.textContent = 'v' + FH.core.version;

      // sidebar nav
      document.querySelectorAll('.fh-nav__link').forEach(function (a) {
        a.addEventListener('click', function (e) {
          e.preventDefault();
          FH.core.go(a.dataset.route);
        });
      });

      // mock toggle — preserve whatever the head script (server-injected) set
      const mock = document.getElementById('fh-mockmode');
      // If the server already set window.MOCK_MODE (server.py injects it into
      // the served HTML), sync the checkbox to match; otherwise use the
      // checkbox's HTML default.
      if (typeof window.MOCK_MODE === 'boolean') {
        mock.checked = window.MOCK_MODE;
      } else {
        window.MOCK_MODE = !!mock.checked;
      }
      mock.addEventListener('change', function () {
        window.MOCK_MODE = !!mock.checked;
        FH.core.setLive(!mock.checked);
      });
      FH.core.setLive(!window.MOCK_MODE);

      // error dismiss
      document.getElementById('fh-error-dismiss').addEventListener('click', FH.core.clearError);

      // overlay close
      document.getElementById('fh-overlay-close').addEventListener('click', FH.core.closeOverlay);
      document.getElementById('fh-overlay').addEventListener('click', function (e) {
        if (e.target.id === 'fh-overlay') FH.core.closeOverlay();
      });

      // keyboard
      FH.core.initKeyboard();

      // hash routing
      window.addEventListener('hashchange', FH.core.applyHash);
      FH.core.applyHash();

      // sections
      FH.search.init();
      FH.results.init();
      FH.mistakes.init();
      FH.watchlist.init();
      FH.spots.init();
      FH.balances.init();
      FH.settings.init();
      FH.flex.init();

      // First-paint sidebar badges from cached state. Cheap; no network.
      try { FH.core.updateBadges(); } catch (_) {}

      // Wire up empty-state CTAs that point to other sections / actions.
      const emptyRefresh = document.getElementById('fh-mistakes-empty-refresh');
      if (emptyRefresh) {
        emptyRefresh.addEventListener('click', function () {
          const btn = document.getElementById('fh-mistakes-refresh');
          if (btn) btn.click();
        });
      }
      const emptyWatchNew = document.getElementById('fh-watch-empty-new');
      if (emptyWatchNew) {
        emptyWatchNew.addEventListener('click', function () {
          const btn = document.getElementById('fh-watch-new');
          if (btn) btn.click();
        });
      }

      // Bind the sticky-header scroll-shadow listener on every table wrap.
      FH.core.initTableScrollShadow();

      FH.core.footer('Ready.', 0);
    },

    go: function (route) {
      if (!FH.core.routes.includes(route)) route = 'search';
      window.location.hash = '#' + route;
    },

    applyHash: function () {
      const h = (window.location.hash || '#search').replace(/^#/, '');
      const route = FH.core.routes.includes(h) ? h : 'search';

      // Section reveal + fade-in. Set data-transition="in" so the CSS
      // animation keyframe runs each navigation; remove it on the next
      // frame to allow re-triggering on subsequent hash changes.
      document.querySelectorAll('.fh-section').forEach(function (s) {
        const becameVisible = s.hidden && s.dataset.route === route;
        s.hidden = s.dataset.route !== route;
        if (becameVisible) {
          // Restart the CSS animation by toggling the attribute off then on
          // on the next animation frame. Browsers debounce identical attribute
          // writes, so the rAF dance is needed to actually replay.
          s.removeAttribute('data-transition');
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              s.setAttribute('data-transition', 'in');
            });
          });
        }
      });
      // Nav: toggle active, and on the freshly-activated link kick the
      // underline-draw animation (1 frame off, 1 frame on).
      document.querySelectorAll('.fh-nav__link').forEach(function (a) {
        const isActive = a.dataset.route === route;
        const wasActive = a.classList.contains('fh-nav__link--active');
        a.classList.toggle('fh-nav__link--active', isActive);
        a.removeAttribute('data-underline');
        if (isActive && !wasActive) {
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              a.setAttribute('data-underline', 'draw');
            });
          });
        }
      });
      document.getElementById('fh-topbar-route').textContent = '/' + route.toUpperCase();

      // lazy-load on first view
      if (route === 'sweet-spots') FH.spots.loadOnce();
      if (route === 'mistakes')    { FH.mistakes.loadOnce(); FH.core.markMistakesRead(); }
      if (route === 'watchlist')   FH.watchlist.refresh();
      if (route === 'balances')    FH.balances.loadOnce();
      if (route === 'settings')    FH.settings.loadOnce();
    },

    setLive: function (live) {
      const dot  = document.getElementById('fh-livedot');
      const text = document.getElementById('fh-livetext');
      dot.textContent = live ? '●' : '○';
      dot.classList.toggle('fh-dot--on',  live);
      dot.classList.toggle('fh-dot--off', !live);
      text.textContent = live ? 'Live' : 'Offline';
    },

    showError: function (msg) {
      const bar = document.getElementById('fh-error');
      document.getElementById('fh-error-msg').textContent = msg;
      bar.hidden = false;
      bar.style.display = 'flex';
      // Scroll the banner into view so the user actually sees it (it lives at
      // the top of the page above the sticky topbar, easy to miss when the
      // user is focused on the Search button at the bottom of the form).
      try { bar.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (_) {}
      // Auto-dismiss after 6 seconds so the page doesn't keep a stale error.
      if (FH.core._errorTimer) clearTimeout(FH.core._errorTimer);
      FH.core._errorTimer = setTimeout(FH.core.clearError, 6000);
    },
    clearError: function () {
      const bar = document.getElementById('fh-error');
      bar.hidden = true;
      bar.style.display = 'none';
      // Cancel any pending auto-dismiss so a stale 6s timer can't no-op on
      // an already-hidden banner (harmless today, but keeps the timer set
      // tidy if the dismiss path changes later).
      if (FH.core._errorTimer) {
        clearTimeout(FH.core._errorTimer);
        FH.core._errorTimer = null;
      }
    },

    footer: function (left, recordCount) {
      document.getElementById('fh-footer-left').textContent  = left;
      // The footer right slot is just the numeric count now — the "Records"
      // label lives in static HTML as a sibling .fh-mini span.
      document.getElementById('fh-footer-right').textContent =
        (typeof recordCount === 'number' ? String(recordCount) : '–');
    },

    // Set a numeric badge on a sidebar nav link. Count > 0 shows the badge;
    // count <= 0 removes it. Used by mistakes (unread) and watchlist (alerts).
    setBadge: function (route, count) {
      const link = document.querySelector('.fh-nav__link[data-route="' + route + '"]');
      if (!link) return;
      let badge = link.querySelector('.fh-nav__badge');
      if (count && count > 0) {
        if (!badge) {
          badge = document.createElement('span');
          badge.className = 'fh-nav__badge';
          link.appendChild(badge);
        }
        badge.textContent = String(count);
      } else if (badge) {
        badge.remove();
      }
    },
    // Pull whatever cached state we have and re-paint sidebar badges. Cheap
    // to call repeatedly; no network. Mistakes: compare current seen-ids in
    // localStorage against the last-rendered ids to derive unread count.
    updateBadges: function () {
      try {
        const seenRaw = localStorage.getItem('fh.mistakes.seen') || '[]';
        const readRaw = localStorage.getItem('fh.mistakes.read') || '[]';
        const seen = JSON.parse(seenRaw) || [];
        const read = new Set(JSON.parse(readRaw) || []);
        const unread = seen.filter(function (id) { return !read.has(id); }).length;
        FH.core.setBadge('mistakes', unread);
      } catch (_) { /* tolerate localStorage quirks */ }
    },
    // Mark all currently-known mistake ids as read. Wired to the mistakes
    // route activation so navigating to the tab clears the unread badge.
    markMistakesRead: function () {
      try {
        const seenRaw = localStorage.getItem('fh.mistakes.seen') || '[]';
        localStorage.setItem('fh.mistakes.read', seenRaw);
        FH.core.updateBadges();
      } catch (_) {}
    },

    // -----------------------------------------------------------------
    // TOAST — slide-in bottom-right notifications.
    //   FH.core.toast('Watch saved', 'success'?)
    //   types: 'info' (default) | 'success' | 'warn'
    // Stacks newest at the bottom; max 3 visible; auto-dismiss at 4s.
    // The toast container lives in static HTML (#fh-toasts). The CSS
    // animation handles the slide-in; the leaving state plays a 160ms
    // slide-out before the node is removed.
    // -----------------------------------------------------------------
    toast: function (msg, type) {
      const host = document.getElementById('fh-toasts');
      if (!host) return;
      const cls = (type === 'success' || type === 'warn') ? type : 'info';

      // Prune to 2 before adding so 3 is the max steady-state.
      while (host.children.length >= 3) {
        host.removeChild(host.firstChild);
      }

      const el = document.createElement('div');
      el.className = 'fh-toast fh-toast--' + cls;
      el.setAttribute('role', 'status');
      el.innerHTML =
        '<span class="fh-toast__msg"></span>' +
        '<button type="button" class="fh-toast__x" aria-label="Dismiss">×</button>';
      el.querySelector('.fh-toast__msg').textContent = String(msg == null ? '' : msg);

      const dismiss = function () {
        if (!el.parentNode) return;
        el.classList.add('fh-toast--leaving');
        // After the 160ms slide-out, remove the node. Use animationend
        // when available; fall back to a setTimeout for older WebKits.
        const remove = function () {
          if (el.parentNode) el.parentNode.removeChild(el);
        };
        let done = false;
        const once = function () { if (!done) { done = true; remove(); } };
        el.addEventListener('animationend', once, { once: true });
        setTimeout(once, 220);
      };
      el.querySelector('.fh-toast__x').addEventListener('click', dismiss);
      host.appendChild(el);

      // Auto-dismiss after 4s.
      setTimeout(dismiss, 4000);
    },

    openOverlay: function (title, htmlBody) {
      document.getElementById('fh-overlay-title').textContent = title;
      document.getElementById('fh-overlay-body').innerHTML = htmlBody;
      const ov = document.getElementById('fh-overlay');
      ov.hidden = false;
      ov.style.display = 'flex';
    },
    closeOverlay: function () {
      const ov = document.getElementById('fh-overlay');
      ov.hidden = true;
      ov.style.display = 'none';
      // Drop any active-row marker from data tables so the next overlay
      // open starts clean.
      document.querySelectorAll('tr.fh-row--active').forEach(function (tr) {
        tr.classList.remove('fh-row--active');
      });
    },
    // Mark a single <tr> as the active/expanded row. Pass null/no arg to
    // just clear all markers. The active row gets the accent border-left
    // + accent-soft background per .fh-row--active CSS.
    setActiveRow: function (tr) {
      document.querySelectorAll('tr.fh-row--active').forEach(function (x) {
        if (x !== tr) x.classList.remove('fh-row--active');
      });
      if (tr) tr.classList.add('fh-row--active');
    },
    // Toggle .fh-table-wrap--scrolled on a wrap when the user has scrolled
    // past the top, so the sticky <th> bottom border thickens. Lightweight
    // — a single rAF-throttled scroll listener per wrap, bound once.
    initTableScrollShadow: function () {
      document.querySelectorAll('.fh-table-wrap').forEach(function (wrap) {
        if (wrap.dataset.fhScrollBound === '1') return;
        wrap.dataset.fhScrollBound = '1';
        let ticking = false;
        const update = function () {
          ticking = false;
          wrap.classList.toggle('fh-table-wrap--scrolled', wrap.scrollTop > 0);
        };
        wrap.addEventListener('scroll', function () {
          if (!ticking) {
            ticking = true;
            requestAnimationFrame(update);
          }
        }, { passive: true });
      });
    },

    initKeyboard: function () {
      let chord = null;
      let chordTimer = null;
      const map = {
        s: 'search', r: 'results', m: 'mistakes', w: 'watchlist',
        p: 'sweet-spots', b: 'balances', ',': 'settings', f: 'flex'
      };

      document.addEventListener('keydown', function (e) {
        const tag = (document.activeElement && document.activeElement.tagName) || '';
        const inField = /^(INPUT|TEXTAREA|SELECT)$/.test(tag);

        if (e.key === 'Escape') {
          FH.core.closeOverlay();
          FH.core.clearError();
          if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
          return;
        }
        if (inField) return;

        if (e.key === '/') {
          e.preventDefault();
          FH.core.go('search');
          setTimeout(function () {
            const el = document.getElementById('fh-origin-input');
            if (el) el.focus();
          }, 0);
          return;
        }

        if (chord === 'g') {
          if (map[e.key]) { FH.core.go(map[e.key]); }
          chord = null;
          if (chordTimer) clearTimeout(chordTimer);
          return;
        }
        if (e.key === 'g') {
          chord = 'g';
          if (chordTimer) clearTimeout(chordTimer);
          chordTimer = setTimeout(function () { chord = null; }, 900);
        }
      });
    },

    api: async function (path, opts) {
      try {
        const r = await fetch(path, Object.assign({
          headers: { 'Content-Type': 'application/json' }
        }, opts || {}));
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return await r.json();
      } catch (err) {
        FH.core.showError(err.message || String(err));
        throw err;
      }
    },

    fmtMoney: function (n) {
      if (n === null || n === undefined || isNaN(n)) return '—';
      return '$' + Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 });
    },
    fmtMiles: function (n) {
      if (!n) return '—';
      return Number(n).toLocaleString('en-US');
    },
    fmtDur: function (mins) {
      if (!mins) return '—';
      const h = Math.floor(mins / 60);
      const m = mins % 60;
      if (h === 0) return m + 'm';
      if (m === 0) return h + 'h';
      return h + 'h ' + (m < 10 ? '0' : '') + m + 'm';
    },
    fmtDate: function (iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      const pad = function (n) { return n < 10 ? '0' + n : '' + n; };
      return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate())
        + ' ' + pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + 'Z';
    },
    // Pretty date/time for flight depart: "2026-06-05T13:50" → "Jun 5 · 13:50"
    fmtDepart: function (iso) {
      if (!iso) return '—';
      // Strip timezone marker for parsing reliability.
      const clean = String(iso).replace(/Z$/, '');
      const parts = clean.split('T');
      const datePart = parts[0];
      const timePart = parts[1] || '';
      const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      const dm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(datePart);
      if (!dm) return iso;
      const mo = months[parseInt(dm[2], 10) - 1] || dm[2];
      const dd = parseInt(dm[3], 10);
      let out = mo + ' ' + dd;
      if (timePart) {
        const tm = /^(\d{2}):(\d{2})/.exec(timePart);
        if (tm) out += ' · ' + tm[1] + ':' + tm[2];
      }
      return out;
    },
    escape: function (s) {
      return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
      });
    },

    riskBadge: function (r) {
      // Defensive: undefined/null/empty risk renders a neutral dash, not a colored "undefined" pill.
      if (r === undefined || r === null || r === '') {
        return '<span class="fh-risk">-</span>';
      }
      const map = { LEGAL: 'fh-risk--legal', GRAY: 'fh-risk--gray', 'TOS-RISK': 'fh-risk--tos' };
      const key = String(r).toUpperCase();
      const cls = map[key] || 'fh-risk--gray';
      return '<span class="fh-risk ' + cls + '">' + FH.core.escape(key) + '</span>';
    },

    sortTable: function (tableId, getRows, render) {
      const tbl = document.getElementById(tableId);
      const ths = tbl.querySelectorAll('thead th[data-sort]');
      ths.forEach(function (th) {
        th.addEventListener('click', function () {
          const key = th.dataset.sort;
          let dir = th.classList.contains('fh-sort--asc') ? 'desc' : 'asc';
          ths.forEach(function (x) { x.classList.remove('fh-sort--asc', 'fh-sort--desc'); });
          th.classList.add(dir === 'asc' ? 'fh-sort--asc' : 'fh-sort--desc');
          const rows = getRows().slice();
          const isMissing = function (v) { return v === undefined || v === null || (typeof v === 'number' && isNaN(v)); };
          rows.sort(function (a, b) {
            const av = a[key], bv = b[key];
            const am = isMissing(av), bm = isMissing(bv);
            if (am && bm) return 0;
            // Missing values always sort to the bottom, regardless of direction.
            if (am) return 1;
            if (bm) return -1;
            if (typeof av === 'number' && typeof bv === 'number') return dir === 'asc' ? av - bv : bv - av;
            // Mixed types (one number, one string): coerce both to numbers if possible.
            const an = typeof av === 'number' ? av : parseFloat(av);
            const bn = typeof bv === 'number' ? bv : parseFloat(bv);
            if (!isNaN(an) && !isNaN(bn) && (typeof av === 'number' || typeof bv === 'number')) {
              return dir === 'asc' ? an - bn : bn - an;
            }
            const as = String(av).toLowerCase(), bs = String(bv).toLowerCase();
            if (as < bs) return dir === 'asc' ? -1 : 1;
            if (as > bs) return dir === 'asc' ?  1 : -1;
            return 0;
          });
          render(rows);
        });
      });
    }
  };

  // ============================================================ //
  // SEARCH                                                       //
  // ============================================================ //

  FH.search = {
    state: { origin: [], dest: [] },

    init: function () {
      FH.search.bindChips('origin');
      FH.search.bindChips('dest');

      document.getElementById('fh-search-form').addEventListener('submit', function (e) {
        e.preventDefault();
        FH.search.submit();
      });
      document.getElementById('fh-search-reset').addEventListener('click', FH.search.reset);
      document.getElementById('fh-search-savewatch').addEventListener('click', FH.search.saveAsWatch);

      // One-way toggle: clear + disable return-date inputs when checked so
      // they can't be silently sent into a one-way search.
      const oneway = document.getElementById('fh-oneway');
      if (oneway) {
        oneway.addEventListener('change', FH.search.applyOneWay);
        FH.search.applyOneWay();
      }
    },

    applyOneWay: function () {
      const oneway = document.getElementById('fh-oneway');
      const rf = document.getElementById('fh-return-from');
      if (!oneway || !rf) return;
      const on = !!oneway.checked;
      if (on) { rf.value = ''; }
      rf.disabled = on;
    },

    bindChips: function (kind) {
      const input = document.getElementById('fh-' + kind + '-input');
      const list  = document.getElementById('fh-suggest-' + kind);

      let active = -1;
      let cache  = [];

      // Race-protection state: every keystroke bumps reqSeq so an older
      // in-flight response (slow network) can't overwrite the suggestions
      // when typing accelerates ("L" -> "LO" -> "LON"). Combined with a
      // short debounce, this kills both the network race and the wasted
      // fetches that the original keystroke-per-fetch loop fired off.
      let reqSeq = 0;
      let debounceTimer = null;
      const DEBOUNCE_MS = 80;

      function render() {
        const chips = document.getElementById('fh-chips-' + kind);
        chips.innerHTML = '';
        FH.search.state[kind].forEach(function (iata) {
          const chip = document.createElement('span');
          chip.className = 'fh-chip';
          chip.innerHTML = FH.core.escape(iata) + ' <span class="fh-chip__x">x</span>';
          chip.querySelector('.fh-chip__x').addEventListener('click', function () {
            FH.search.state[kind] = FH.search.state[kind].filter(function (c) { return c !== iata; });
            render();
          });
          chips.appendChild(chip);
        });
      }
      render();

      function close() { list.hidden = true; active = -1; }
      function add(iata) {
        iata = (iata || '').trim().toUpperCase();
        if (!iata) return;
        if (!FH.search.state[kind].includes(iata)) FH.search.state[kind].push(iata);
        input.value = '';
        close();
        render();
      }

      async function doFetch(q, mySeq) {
        try {
          const data = await fetch('/api/hubs?q=' + encodeURIComponent(q))
            .then(function (r) { return r.json(); });
          // Discard stale response — a newer keystroke supersedes us.
          if (mySeq !== reqSeq) return;
          // Also discard if the input has been cleared/diverged from q.
          if (input.value.trim() !== q) return;
          cache = data.hubs || [];
          if (!cache.length) { close(); return; }
          // IATA codes come from a server-controlled dataset (uppercase,
          // 3 chars) but we escape them anyway as defense-in-depth — if
          // the hub dataset ever grows a hostile entry the autocomplete
          // dropdown must not become an XSS sink.
          list.innerHTML = cache.map(function (h, i) {
            const code = FH.core.escape(h.iata);
            return '<li data-iata="' + code + '" data-i="' + i + '">'
              + '<span class="fh-suggest__code">' + code + '</span>'
              + '<span class="fh-suggest__name">' + FH.core.escape(h.city)
              + ' (' + FH.core.escape(h.country) + ')</span>'
              + '</li>';
          }).join('');
          list.hidden = false;
          active = -1;
          list.querySelectorAll('li').forEach(function (li) {
            li.addEventListener('mousedown', function (e) {
              e.preventDefault();
              add(li.dataset.iata);
            });
          });
        } catch (e) {
          if (mySeq === reqSeq) close();
        }
      }

      input.addEventListener('input', function () {
        const q = input.value.trim();
        // Bump the sequence on every keystroke so any in-flight request is
        // immediately tagged stale, even before the debounce timer fires.
        reqSeq += 1;
        const mySeq = reqSeq;
        if (debounceTimer) clearTimeout(debounceTimer);
        if (!q) { close(); return; }
        debounceTimer = setTimeout(function () { doFetch(q, mySeq); }, DEBOUNCE_MS);
      });

      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (active >= 0 && cache[active]) add(cache[active].iata);
          else if (input.value) add(input.value);
          return;
        }
        if (e.key === ',') {
          e.preventDefault();
          if (input.value) add(input.value);
          return;
        }
        if (e.key === 'Backspace' && !input.value && FH.search.state[kind].length) {
          FH.search.state[kind].pop();
          render();
          return;
        }
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (!cache.length) return;
          active = (active + 1) % cache.length;
          FH.search.markActive(list, active);
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (!cache.length) return;
          active = (active - 1 + cache.length) % cache.length;
          FH.search.markActive(list, active);
          return;
        }
        if (e.key === 'Escape') { close(); }
      });

      input.addEventListener('blur', function () { setTimeout(close, 120); });
    },

    markActive: function (list, idx) {
      list.querySelectorAll('li').forEach(function (li, i) {
        li.classList.toggle('fh-suggest--active', i === idx);
      });
    },

    // Re-paint a chip strip from FH.search.state[kind]. The chip render()
    // inside bindChips() is a local closure, so external mutators (e.g.
    // mistakes COPY ROUTE writing state.origin = [parts[0]]) need this
    // shared painter to make the chips appear without re-binding listeners.
    // Removal still goes through the closure's filter — we re-use its
    // [x] markup so the existing listener catches new chips? No — listeners
    // are attached per-chip inside the closure, so DOM we paint here
    // wouldn't have them. Instead, just write the visual chips; if the
    // user wants to remove a chip they can hit Backspace in the input
    // (bindChips' keydown reads state directly and re-renders).
    paintChips: function (kind) {
      const chips = document.getElementById('fh-chips-' + kind);
      if (!chips) return;
      chips.innerHTML = '';
      FH.search.state[kind].forEach(function (iata) {
        const chip = document.createElement('span');
        chip.className = 'fh-chip';
        chip.innerHTML = FH.core.escape(iata) + ' <span class="fh-chip__x">x</span>';
        chip.querySelector('.fh-chip__x').addEventListener('click', function () {
          FH.search.state[kind] = FH.search.state[kind].filter(function (c) { return c !== iata; });
          FH.search.paintChips(kind);
        });
        chips.appendChild(chip);
      });
    },

    // Read an integer input, preserving user-entered 0 (parseInt|| would coerce
     // 0 to the default). Returns `dflt` only when the field is blank/NaN, then
     // clamps to [min,max].
    readInt: function (id, dflt, min, max) {
      const el = document.getElementById(id);
      const raw = el ? el.value : '';
      let n = (raw === '' || raw === null || raw === undefined) ? dflt : parseInt(raw, 10);
      if (!Number.isFinite(n)) n = dflt;
      if (typeof min === 'number' && n < min) n = min;
      if (typeof max === 'number' && n > max) n = max;
      return n;
    },

    collect: function () {
      const cabin = Array.from(document.querySelectorAll('input[name=cabin]:checked')).map(function (c) { return c.value; });
      const modeEl = document.querySelector('input[name=fh-mode]:checked');
      const oneWay = document.getElementById('fh-oneway').checked;
      const departVal = document.getElementById('fh-depart-from').value || null;
      const returnVal = oneWay ? null : (document.getElementById('fh-return-from').value || null);
      return {
        origins:       FH.search.state.origin.slice(),
        destinations:  FH.search.state.dest.slice(),
        depart:        departVal,
        return:        returnVal,
        // Also send legacy range fields for server backward-compat (server's
        // effective_depart/return reads depart first, then range fallbacks).
        depart_from:   departVal,
        depart_to:     departVal,
        return_from:   returnVal,
        return_to:     returnVal,
        one_way:       oneWay,
        cabin:         cabin.length ? cabin : ['Y'],
        adults:        FH.search.readInt('fh-adults',   1, 1, 8),
        children:      FH.search.readInt('fh-children', 0, 0, 8),
        infants:       FH.search.readInt('fh-infants',  0, 0, 4),
        max_stops:     FH.search.readInt('fh-maxstops', 2, 0, 5),
        max_hours:     FH.search.readInt('fh-maxhours', 36, 1, 72),
        composers: {
          positioning:     document.getElementById('fh-comp-pos').checked,
          hidden_city:     document.getElementById('fh-comp-hidden').checked,
          open_jaw:        document.getElementById('fh-comp-openjaw').checked,
          stopover_gaming: document.getElementById('fh-comp-stopgame').checked
        },
        expand_origins:      (document.getElementById('fh-expand-origins') || {checked: true}).checked,
        expand_destinations: (document.getElementById('fh-expand-destinations') || {checked: true}).checked,
        mode:          (modeEl && modeEl.value) || 'both'
      };
    },

    submit: async function () {
      const q = FH.search.collect();
      if (!q.origins.length || !q.destinations.length) {
        FH.core.showError('Please add at least one origin and one destination.');
        return;
      }
      if (!q.depart) {
        FH.core.showError('Please pick a depart date.');
        const dep = document.getElementById('fh-depart-from');
        if (dep) { dep.focus(); try { dep.showPicker && dep.showPicker(); } catch (_) {} }
        return;
      }
      if (!q.one_way && q.return && q.return < q.depart) {
        FH.core.showError('Return date must be on or after the depart date.');
        const ret = document.getElementById('fh-return-from');
        if (ret) ret.focus();
        return;
      }
      const prog = document.getElementById('fh-search-progress');
      const lbl  = document.getElementById('fh-search-progress-label');
      // Force visible — inline display:none in HTML beats `[hidden]` removal,
      // so toggle both for guaranteed visibility.
      prog.hidden = false;
      prog.style.display = '';
      // Disable the submit button so user can't double-click
      const btn = document.getElementById('fh-search-go');
      if (btn) { btn.disabled = true; btn.textContent = 'Searching…'; }
      // Pulse the top-bar live dot to signal "search running" — purely visual.
      const liveDot = document.getElementById('fh-livedot');
      if (liveDot) liveDot.classList.add('fh-dot--pulse');
      // Scroll progress bar into view so user definitely sees it.
      try { prog.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (_) {}

      const t0 = performance.now();
      // Rough expected duration: ~3s per route × cabin combo (Google
      // Flights + Seats.aero in parallel), capped to a sane band so the
      // counter never reads "1%" for the first 9 seconds of a 1-pair
      // search. Empirical numbers from the running server.
      const pairs = Math.max(1, q.origins.length * q.destinations.length * (q.cabin || []).length);
      const expectedSec = Math.max(8, Math.min(45, pairs * 3));
      // Live elapsed counter + percentage chip — reassures the user the
      // search hasn't hung. The chip stays separate so the line wraps
      // cleanly on narrow viewports.
      const updateLabel = function () {
        const sec = Math.floor((performance.now() - t0) / 1000);
        const note = pairs > 1
          ? pairs + ' routes × ' + q.cabin.length + ' cabin' + (q.cabin.length === 1 ? '' : 's')
          : '1 route × ' + q.cabin.length + ' cabin' + (q.cabin.length === 1 ? '' : 's');
        // Soft cap the % at 95 so the chip never reads "100% / waiting".
        const pct = Math.min(95, Math.floor((sec / expectedSec) * 100));
        lbl.innerHTML = ''
          + FH.core.escape('Searching ' + note + ' — ' + sec + 's')
          + '<span class="fh-progress__chip">'
          +   FH.core.escape('est ' + expectedSec + 's · ' + pct + '%')
          + '</span>';
      };
      updateLabel();
      const progressTimer = setInterval(updateLabel, 500);
      try {
        const data = await FH.core.api('/api/search', {
          method: 'POST',
          body: JSON.stringify(q)
        });
        const ms = Math.round(performance.now() - t0);
        const results = data.results || [];
        FH.results.setData(results);
        // If the server auto-expanded to nearby airports, surface it so the
        // user knows which airports were actually searched.
        const meta = data.meta || {};
        const oAdd = (meta.origins_added || []).join(', ');
        const dAdd = (meta.destinations_added || []).join(', ');
        let expandedNote = '';
        if (oAdd) expandedNote += ' · +origin: ' + oAdd;
        if (dAdd) expandedNote += ' · +dest: ' + dAdd;
        FH.core.footer(
          'Search @ ' + new Date().toLocaleTimeString() + ' (' + ms + 'ms)' + expandedNote,
          results.length
        );
        if (results.length === 0) {
          FH.core.toast('Search returned no results — try widening dates or cabins', 'warn');
        }
        FH.core.go('results');
      } catch (e) {
        // showError already fired
      } finally {
        clearInterval(progressTimer);
        prog.hidden = true;
        prog.style.display = 'none';
        if (btn) { btn.disabled = false; btn.textContent = 'Search flights'; }
        if (liveDot) liveDot.classList.remove('fh-dot--pulse');
      }
    },

    reset: function () {
      FH.search.state.origin = [];
      FH.search.state.dest   = [];
      document.querySelectorAll('.fh-chips').forEach(function (c) { c.innerHTML = ''; });
      document.getElementById('fh-search-form').reset();
      // form.reset() restores HTML defaults (oneway unchecked); re-sync the
      // return-date enabled/disabled state to match.
      FH.search.applyOneWay();
      // Also hide the progress bar in case reset is hit mid-search.
      const prog = document.getElementById('fh-search-progress');
      if (prog) { prog.hidden = true; prog.style.display = 'none'; }
      const btn = document.getElementById('fh-search-go');
      if (btn) { btn.disabled = false; btn.textContent = 'Search flights'; }
    },

    saveAsWatch: async function () {
      const q = FH.search.collect();
      // Match /search validation: a watch with no origin or destination is
      // un-runnable and the server will 400 it anyway, so fail fast with a
      // message the user can act on.
      if (!q.origins.length || !q.destinations.length) {
        FH.core.showError('Add at least one origin and one destination before saving as a watch.');
        return;
      }
      // A watch with no depart date silently falls back to "today" on the
      // server, which is almost never what the user meant. Force them to
      // pick a date so the saved watch matches the search they typed.
      if (!q.depart) {
        FH.core.showError('Pick a depart date before saving as a watch.');
        return;
      }
      if (!q.one_way && q.return && q.return < q.depart) {
        FH.core.showError('Return date must be on or after the depart date.');
        return;
      }
      const departVal = q.depart;
      const returnVal = q.one_way ? null : q.return;
      // Preserve the full search context in the watch — origins, destinations,
      // depart window, return window (only when there is a real return),
      // cabin set (not just the first one), passengers, and mode/composers —
      // so the next run reproduces what the user actually searched for.
      const body = {
        origins: q.origins.slice(),
        destinations: q.destinations.slice(),
        window_from: departVal,
        window_to:   departVal,
        depart_window: { from: departVal, to: departVal },
        max_usd: 600,
        cabin:  (q.cabin && q.cabin[0]) || 'Y',
        cabins: (q.cabin && q.cabin.length) ? q.cabin.slice() : ['Y'],
        adults:   q.adults   || 1,
        children: q.children || 0,
        infants:  q.infants  || 0,
        mode:     q.mode     || 'both',
        composers: q.composers || {}
      };
      if (returnVal) {
        body.return_window = { from: returnVal, to: returnVal };
      }
      try {
        await FH.core.api('/api/watchlist', { method: 'POST', body: JSON.stringify(body) });
        FH.core.footer(
          'Watch saved @ ' + new Date().toLocaleTimeString() + ' — ' +
          q.origins.join('/') + ' → ' + q.destinations.join('/'),
          null
        );
        FH.core.toast('Watch saved — ' + q.origins.join('/') + ' → ' + q.destinations.join('/'), 'success');
        FH.core.go('watchlist');
      } catch (e) { /* showError already fired by FH.core.api */ }
    }
  };

  // ============================================================ //
  // RESULTS                                                      //
  // ============================================================ //

  FH.results = {
    raw: [],
    view: [],
    activeFilter: 'all',

    init: function () {
      document.querySelectorAll('#fh-results-filters .fh-chip--filter').forEach(function (b) {
        b.addEventListener('click', function () {
          document.querySelectorAll('#fh-results-filters .fh-chip--filter').forEach(function (x) {
            x.classList.remove('fh-chip--on');
          });
          b.classList.add('fh-chip--on');
          FH.results.activeFilter = b.dataset.filter;
          FH.results.render();
        });
      });

      FH.core.sortTable('fh-results-table', function () { return FH.results.view; }, function (rows) {
        FH.results.view = rows;
        FH.results.renderRows(rows);
      });
    },

    setData: function (rows) {
      const safe = Array.isArray(rows) ? rows : [];
      FH.results.raw  = safe;
      FH.results.view = safe.slice();
      FH.results.render();
    },

    applyFilter: function (rows) {
      const f = FH.results.activeFilter;
      if (f === 'all')   return rows;
      if (f === 'legal') return rows.filter(function (r) { return r.risk === 'LEGAL'; });
      if (f === 'gray')  return rows.filter(function (r) { return r.risk !== 'TOS-RISK'; });
      if (f === 'award') return rows.filter(function (r) { return !!r.is_award; });
      if (f === 'cash')  return rows.filter(function (r) { return !r.is_award; });
      if (f === 'J')     return rows.filter(function (r) {
        const code = r.cabin_code || r.cabin;
        return code === 'J' || code === 'F' ||
               r.cabin === 'Business' || r.cabin === 'First';
      });
      return rows;
    },

    render: function () {
      const rows = FH.results.applyFilter(FH.results.raw);
      FH.results.view = rows.slice();
      FH.results.renderRows(rows);
    },

    setQualityBanner: function (url, incomplete, total) {
      let banner = document.getElementById('fh-results-quality');
      if (!url) {
        if (banner) banner.remove();
        return;
      }
      if (!banner) {
        const tableWrap = document.querySelector('#fh-section-results .fh-table-wrap');
        if (!tableWrap) return;
        banner = document.createElement('div');
        banner.id = 'fh-results-quality';
        banner.className = 'fh-quality-banner';
        tableWrap.parentNode.insertBefore(banner, tableWrap);
      }
      banner.innerHTML =
        '<span>' + incomplete + ' of ' + total + ' results missing duration / airline ' +
        '(Google Flights data quality varies by route).</span> ' +
        '<a class="fh-btn fh-btn--mini fh-btn--primary fh-arrow-link" href="' + FH.core.escape(url) +
        '" target="_blank" rel="noopener">View all on Google Flights <span class="fh-arrow-link__arrow">→</span></a>';
    },

    renderRows: function (rows) {
      const tb = document.getElementById('fh-results-tbody');
      if (!rows.length) {
        tb.innerHTML = '<tr class="fh-empty"><td colspan="13">'
          + '<div class="fh-empty__glyph">⊘</div>'
          + '<div class="fh-empty__text">No results yet. Run a search.</div>'
          + '</td></tr>';
        FH.core.footer('No results @ ' + new Date().toLocaleTimeString(), 0);
        FH.results.setQualityBanner(null);
        return;
      }
      // Top-deal mark: lowest total_usd among rows with a real number gets a
      // ★ pin in column 1. Quiet typographic flourish, no extra row.
      let topDealIdx = -1;
      let topDealUsd = Infinity;
      rows.forEach(function (r, i) {
        if (typeof r.total_usd === 'number' && r.total_usd > 0 && r.total_usd < topDealUsd) {
          topDealUsd = r.total_usd;
          topDealIdx = i;
        }
      });
      // If most rows are missing duration / carrier (fast-flights returned
      // price-only stubs), surface a banner with a Google Flights link so the
      // user has an escape hatch.
      const incomplete = rows.filter(function (r) {
        return !r.duration_min || !r.carrier || r.carrier === '—';
      }).length;
      const firstGF = rows.find(function (r) { return r.google_flights_url || r.deep_link; });
      if (incomplete >= rows.length / 2 && firstGF) {
        FH.results.setQualityBanner(firstGF.google_flights_url || firstGF.deep_link, incomplete, rows.length);
      } else {
        FH.results.setQualityBanner(null);
      }
      const compLabel = {
        positioning: 'POS',
        hidden_city: 'HC',
        open_jaw:    'OJ',
        stopover:    'SO'
      };
      tb.innerHTML = rows.map(function (r, i) {
        const comp = r.composition || {};
        const ctype = comp.type || 'direct';
        const compTag = (ctype !== 'direct' && compLabel[ctype])
          ? ' <span class="fh-risk fh-risk--gray">' + compLabel[ctype] + '</span>'
          : '';
        // Friendly stops label: 0 → Nonstop, 1 → 1 stop, 2 → 2 stops, missing → —
        let stopsDisp;
        if (r.stops === 0)                                                stopsDisp = 'Nonstop';
        else if (typeof r.stops === 'number' && r.stops > 0)              stopsDisp = r.stops + (r.stops === 1 ? ' stop' : ' stops');
        else if (r.stops === undefined || r.stops === null || r.stops === '-') stopsDisp = '—';
        else                                                              stopsDisp = String(r.stops);
        const carrierDisp = r.carrier && String(r.carrier).trim() ? r.carrier : '—';
        // Carrier logo cell: <img> hidden via inline style when we have no
        // URL; this is one DOM-shape regardless of source so CSS can hide
        // the broken-image fallback consistently. onerror swallows 404s
        // from the CDN (e.g. an exotic Z0 / G3 logo we don't have).
        const logoSrc = r.carrier_logo_url || '';
        const logoTag = ''
          + '<img class="fh-carrier__logo" '
          +   'src="' + FH.core.escape(logoSrc) + '" '
          +   'alt="" loading="lazy" '
          +   'onerror="this.style.display=\'none\'"'
          +   (logoSrc ? '' : ' style="display:none"')
          + '/>';
        const carrierCell = ''
          + '<span class="fh-carrier">'
          +   logoTag
          +   '<span class="fh-carrier__name">' + FH.core.escape(carrierDisp) + '</span>'
          + '</span>';
        // Booking-link preference: airline-direct → Google Flights → upstream deep_link.
        // Server now sets deep_link = upstream-or-airline-or-gf so deep_link is a safe
        // final fallback, but we still prefer the more explicit airline_url when present.
        const bookHref = r.airline_url || r.google_flights_url || r.deep_link || '';
        const bookBtn = bookHref
          ? '<a class="fh-btn fh-btn--mini fh-btn--primary fh-arrow-link" href="' + FH.core.escape(bookHref) + '" target="_blank" rel="noopener">Book <span class="fh-arrow-link__arrow">→</span></a> '
          : '';
        const isTopDeal = (i === topDealIdx);
        const rowCls = isTopDeal ? ' class="fh-row--top-deal"' : '';
        const rankCell = isTopDeal
          ? '<span class="fh-top-deal" title="Lowest total in current view">★</span> ' + FH.core.escape(r.rank)
          : FH.core.escape(r.rank);
        return ''
          + '<tr' + rowCls + ' data-i="' + i + '">'
          + '<td>' + rankCell + '</td>'
          + '<td>' + FH.core.escape(r.route) + compTag + '</td>'
          + '<td>' + carrierCell + '</td>'
          + '<td>' + FH.core.escape(FH.core.fmtDepart(r.depart)) + '</td>'
          + '<td class="fh-num">' + FH.core.fmtDur(r.duration_min) + '</td>'
          + '<td class="fh-num">' + FH.core.escape(stopsDisp) + '</td>'
          + '<td>' + FH.core.escape(r.cabin) + '</td>'
          + '<td class="fh-num">' + FH.core.fmtMoney(r.cash_usd) + '</td>'
          + '<td class="fh-num">' + (r.miles ? Number(r.miles).toLocaleString() : '—') + '</td>'
          + '<td class="fh-num">' + (r.miles_value_usd ? FH.core.fmtMoney(r.miles_value_usd) + ' @' + r.miles_cpp_cents + 'c' : '—') + '</td>'
          + '<td class="fh-num fh-mono-em">' + FH.core.fmtMoney(r.total_usd) + '</td>'
          + '<td>' + FH.core.riskBadge(r.risk) + '</td>'
          + '<td>'
          +   bookBtn
          +   '<button class="fh-btn fh-btn--mini" data-act="expand" data-i="' + i + '">Details</button> '
          +   '<button class="fh-btn fh-btn--mini" data-act="watch"  data-i="' + i + '">Watch</button>'
          + '</td>'
          + '</tr>';
      }).join('');

      tb.querySelectorAll('button[data-act="expand"]').forEach(function (b) {
        b.addEventListener('click', function (e) {
          e.stopPropagation();
          const tr = b.closest('tr');
          if (tr) FH.core.setActiveRow(tr);
          FH.results.detail(parseInt(b.dataset.i, 10));
        });
      });
      tb.querySelectorAll('button[data-act="watch"]').forEach(function (b) {
        b.addEventListener('click', async function (e) {
          e.stopPropagation();
          const r = FH.results.view[parseInt(b.dataset.i, 10)];
          if (!r) return;
          const iata = /^[A-Z]{3}$/;
          // Prefer the explicit origin/destination fields (composed itineraries
          // may have route strings like "EWR-LHR (POS:JFK)" that don't split cleanly).
          let origin = (r.origin || '').toUpperCase();
          let dest   = (r.destination || '').toUpperCase();
          if (!iata.test(origin) || !iata.test(dest)) {
            const parts = (r.route || '').split('-').map(function (p) { return p.trim().toUpperCase(); });
            if (parts.length === 2 && iata.test(parts[0]) && iata.test(parts[1])) {
              origin = parts[0]; dest = parts[1];
            } else {
              FH.core.showError('Cannot derive a 3-letter IATA pair from "' + (r.route || '?') + '".');
              return;
            }
          }
          const day = (r.depart || '').slice(0, 10) || new Date().toISOString().slice(0, 10);
          const maxUsd = (typeof r.total_usd === 'number' && r.total_usd > 0)
            ? Math.round(r.total_usd * 0.9)
            : 500;
          try {
            await FH.core.api('/api/watchlist', {
              method: 'POST',
              body: JSON.stringify({
                origins: [origin], destinations: [dest],
                window_from: day,
                window_to:   day,
                max_usd: maxUsd,
                // r.cabin is now the human label ("Economy" / "Business" / ...)
                // while the watchlist + downstream cabin filters expect the
                // single-letter code; prefer cabin_code, fall back to first
                // char of the label as a last-ditch normalizer.
                cabin: r.cabin_code || (r.cabin && String(r.cabin).charAt(0)) || 'Y'
              })
            });
            FH.core.go('watchlist');
          } catch (_) {
            // showError already fired by FH.core.api
          }
        });
      });
      tb.querySelectorAll('tr[data-i]').forEach(function (tr) {
        tr.addEventListener('click', function (e) {
          // Don't hijack clicks that landed on the Book link or any of the
          // action buttons — those handle navigation/state themselves and
          // bubble up to the row otherwise (opening the detail overlay on
          // top of a fresh tab).
          const tgt = e.target;
          if (tgt && tgt.closest && tgt.closest('a,button')) return;
          FH.core.setActiveRow(tr);
          FH.results.detail(parseInt(tr.dataset.i, 10));
        });
      });

      FH.core.footer('Results rendered @ ' + new Date().toLocaleTimeString(), rows.length);
    },

    detail: function (i) {
      const r = FH.results.view[i];
      if (!r) return;
      const segs = (r.segments || []).map(function (s) {
        const slogo = s.carrier_logo_url
          ? '<img class="fh-carrier__logo fh-carrier__logo--seg" src="' + FH.core.escape(s.carrier_logo_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'"/>'
          : '';
        return '<pre class="fh-seg">'
          + slogo
          + FH.core.escape((s.from || '?') + ' → ' + (s.to || '?')) + '  '
          + FH.core.escape((s.carrier || '') + (s.flight || '')) + '  '
          + FH.core.escape((s.dep_local || '?') + '–' + (s.arr_local || '?')) + '  '
          + FH.core.escape(s.aircraft || '') + '  '
          + FH.core.fmtDur(s.duration_min)
          + '</pre>';
      }).join('') || '<pre>(no segment detail returned by source)</pre>';

      // Composition block — surfaces positioning / hidden-city / open-jaw / stopover
      // info that the row cells can't fit. For "direct" rows this section is hidden.
      const comp = r.composition || {};
      const ctype = comp.type || 'direct';
      let compBlock = '';
      if (ctype && ctype !== 'direct') {
        const legs = (comp.legs || []).map(function (l) {
          return '<pre>' + FH.core.escape(JSON.stringify(l)) + '</pre>';
        }).join('') || '<pre>(no leg detail)</pre>';
        const niceType = ({
          positioning: 'Positioning flight',
          hidden_city: 'Hidden-city',
          open_jaw:    'Open-jaw',
          stopover:    'Free stopover',
          direct:      'Direct'
        })[ctype] || ctype;
        compBlock = ''
          + '<h3>Travel-hacking move: ' + FH.core.escape(niceType) + '</h3>'
          + '<div class="fh-kv">'
          +   '<div class="fh-kv__k">Type</div>       <div class="fh-kv__v">' + FH.core.escape(niceType) + '</div>'
          +   '<div class="fh-kv__k">Extra cost</div> <div class="fh-kv__v">' + FH.core.fmtMoney(comp.extra_cost_usd) + '</div>'
          +   '<div class="fh-kv__k">Extra time</div> <div class="fh-kv__v">' + FH.core.fmtDur(comp.extra_time_minutes) + '</div>'
          +   '<div class="fh-kv__k">Risk</div>       <div class="fh-kv__v">' + FH.core.riskBadge(comp.risk) + '</div>'
          +   '<div class="fh-kv__k">Notes</div>      <div class="fh-kv__v">' + FH.core.escape(comp.notes || '—') + '</div>'
          + '</div>'
          + '<h4>Legs</h4>' + legs;
      }

      const bookLinks = [];
      if (r.airline_url) {
        bookLinks.push('<a class="fh-btn fh-btn--mini fh-btn--primary fh-arrow-link" href="' +
          FH.core.escape(r.airline_url) + '" target="_blank" rel="noopener">Book on ' +
          FH.core.escape(r.carrier || 'airline') + ' <span class="fh-arrow-link__arrow">→</span></a>');
      }
      if (r.google_flights_url) {
        bookLinks.push('<a class="fh-btn fh-btn--mini fh-arrow-link" href="' +
          FH.core.escape(r.google_flights_url) +
          '" target="_blank" rel="noopener">Open in Google Flights <span class="fh-arrow-link__arrow">→</span></a>');
      } else if (r.deep_link) {
        bookLinks.push('<a class="fh-btn fh-btn--mini fh-arrow-link" href="' +
          FH.core.escape(r.deep_link) +
          '" target="_blank" rel="noopener">Verify on Google Flights <span class="fh-arrow-link__arrow">→</span></a>');
      }
      const verifyLink = bookLinks.length
        ? '<div class="fh-actions" style="margin:8px 0 16px;">' + bookLinks.join('') + '</div>'
        : '';
      const html = ''
        + '<div class="fh-kv">'
        +   '<div class="fh-kv__k">Route</div>     <div class="fh-kv__v">' + FH.core.escape(r.route) + '</div>'
        +   '<div class="fh-kv__k">Airline</div>   <div class="fh-kv__v">' + FH.core.escape(r.carrier || '—') + '</div>'
        +   '<div class="fh-kv__k">Cabin</div>     <div class="fh-kv__v">' + FH.core.escape(r.cabin) + '</div>'
        +   '<div class="fh-kv__k">Fare class</div><div class="fh-kv__v">' + FH.core.escape(r.fare_class || '—') + '</div>'
        +   '<div class="fh-kv__k">Cash</div>      <div class="fh-kv__v">' + FH.core.fmtMoney(r.cash_usd) + '</div>'
        +   '<div class="fh-kv__k">Miles</div>     <div class="fh-kv__v">' + (r.miles ? Number(r.miles).toLocaleString() + ' @ ' + (r.miles_cpp_cents || '?') + 'c' : '—') + '</div>'
        +   '<div class="fh-kv__k">Total cost</div><div class="fh-kv__v">' + FH.core.fmtMoney(r.total_usd) + '</div>'
        +   '<div class="fh-kv__k">Risk</div>      <div class="fh-kv__v">' + FH.core.riskBadge(r.risk) + '</div>'
        + '</div>'
        + verifyLink
        + compBlock
        + '<h3>Segments</h3>' + segs
        + '<h3>Baggage</h3><pre>' + FH.core.escape(r.baggage || '—') + '</pre>'
        + '<h3>How to book</h3><pre>' + FH.core.escape(r.booking_instructions || '—') + '</pre>';
      FH.core.openOverlay((r.route || '?') + (r.carrier ? ' · ' + r.carrier : ''), html);
    }
  };

  // ============================================================ //
  // MISTAKE FARES                                                //
  // ============================================================ //

  FH.mistakes = {
    loaded: false,
    init: function () {
      document.getElementById('fh-mistakes-refresh').addEventListener('click', FH.mistakes.refresh);
    },
    loadOnce: async function () {
      if (FH.mistakes.loaded) return;
      // First view of the tab — fetch cached feed (fast). The Refresh feed
      // button re-ingests from source.
      document.getElementById('fh-mistakes-status').textContent = 'Pulling feed...';
      try {
        const data = await FH.core.api('/api/mistakes');
        FH.mistakes.render(data.mistakes || []);
        FH.mistakes.loaded = true;
        document.getElementById('fh-mistakes-status').textContent = 'Feed OK @ ' + new Date().toLocaleTimeString();
      } catch (e) {
        document.getElementById('fh-mistakes-status').textContent = 'Feed failed.';
      }
    },
    refresh: async function () {
      // Button label is "Refresh feed" — force re-ingest from sources.
      document.getElementById('fh-mistakes-status').textContent = 'Re-ingesting from sources...';
      // Capture the previously-known mistake id set so we can report
      // "N new" in the success toast.
      let prevIds = [];
      try {
        prevIds = JSON.parse(localStorage.getItem('fh.mistakes.seen') || '[]');
      } catch (_) { prevIds = []; }
      const prevSet = new Set(prevIds);
      try {
        const data = await FH.core.api('/api/mistakes/refresh', { method: 'POST', body: '{}' });
        const rows = data.mistakes || [];
        FH.mistakes.render(rows);
        FH.mistakes.loaded = true;
        document.getElementById('fh-mistakes-status').textContent = 'Feed refreshed @ ' + new Date().toLocaleTimeString();
        const newCount = rows.filter(function (m) {
          return m.id && !prevSet.has(m.id);
        }).length;
        if (newCount > 0) {
          FH.core.toast('Refresh complete — ' + newCount + ' new mistake' + (newCount === 1 ? '' : 's'), 'success');
        } else {
          FH.core.toast('Refresh complete — no new mistakes', 'info');
        }
      } catch (e) {
        document.getElementById('fh-mistakes-status').textContent = 'Refresh failed.';
        FH.core.toast('Refresh failed', 'warn');
      }
    },
    render: function (rows) {
      const wrap = document.getElementById('fh-mistakes-list');
      if (!rows.length) {
        wrap.innerHTML = ''
          + '<div class="fh-empty">'
          +   '<div class="fh-empty__glyph">◇</div>'
          +   '<div class="fh-empty__text">No mistake fares today. Stay alert.</div>'
          + '</div>';
        return;
      }
      // DEAL tag heuristic: among rows with a price, median is the midpoint;
      // any row priced under the median AND tagged risk=LEGAL gets a high-
      // contrast DEAL pill on the card. Updates read state in localStorage
      // so unread counter can show in sidebar.
      const prices = rows
        .map(function (m) { return (typeof m.price === 'number' && m.price > 0) ? m.price : null; })
        .filter(function (p) { return p !== null; })
        .sort(function (a, b) { return a - b; });
      const median = prices.length ? prices[Math.floor(prices.length / 2)] : null;
      try {
        const ids = rows.map(function (m) { return m.id; }).filter(Boolean);
        localStorage.setItem('fh.mistakes.seen', JSON.stringify(ids));
        FH.core.updateBadges && FH.core.updateBadges();
      } catch (_) {}

      wrap.innerHTML = rows.map(function (m) {
        const isDeal = (
          (m.risk === 'LEGAL') &&
          (median !== null) &&
          (typeof m.price === 'number' && m.price > 0 && m.price < median)
        );
        const dealTag = isDeal
          ? ' <span class="fh-deal-tag" title="Below median price and marked LEGAL">DEAL</span>'
          : '';
        const logoTag = m.carrier_logo_url
          ? '<img class="fh-carrier__logo" src="' + FH.core.escape(m.carrier_logo_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'"/>'
          : '';
        const carrierLabel = m.carrier ? FH.core.escape(m.carrier) : '—';
        return ''
          + '<article class="fh-card' + (isDeal ? ' fh-card--deal' : '') + '" data-id="' + FH.core.escape(m.id) + '">'
          + '  <div class="fh-card__head">'
          + '    <span class="fh-card__route">' + FH.core.escape(m.route) + dealTag + '</span>'
          + '    <span class="fh-card__price">' + FH.core.fmtMoney(m.price) + ' / ' + FH.core.escape(m.cabin) + '</span>'
          + '  </div>'
          + '  <div class="fh-card__meta">'
          + '    <span class="fh-carrier">' + logoTag + '<span class="fh-carrier__name">' + carrierLabel + '</span>'
          +        ' <span class="fh-card__src">· ' + FH.core.escape(m.source) + '</span></span>'
          + '    <span>' + FH.core.fmtDate(m.posted_at) + '</span>'
          + '  </div>'
          + '  <div class="fh-card__body">' + FH.core.escape(m.note) + '</div>'
          + '  <div class="fh-card__meta">' + FH.core.riskBadge(m.risk) + '</div>'
          + '  <div class="fh-card__actions">'
          + '    <button class="fh-btn fh-btn--primary fh-btn--mini" data-act="watch" data-id="' + FH.core.escape(m.id) + '">+ Watchlist</button>'
          + '    <button class="fh-btn fh-btn--mini"                  data-act="copy"  data-id="' + FH.core.escape(m.id) + '">Copy route</button>'
          + '  </div>'
          + '</article>';
      }).join('');

      wrap.querySelectorAll('button[data-act="watch"]').forEach(function (b) {
        b.addEventListener('click', async function () {
          const card = b.closest('.fh-card');
          const route = card.querySelector('.fh-card__route').textContent.trim();
          const parts = route.split('-').map(function (p) { return p.trim().toUpperCase(); });
          const iata = /^[A-Z]{3}$/;
          let origin = null, destination = null;
          if (parts.length === 2 && iata.test(parts[0]) && iata.test(parts[1])) {
            origin = parts[0];
            destination = parts[1];
          } else if (FH.search.state.origin.length && FH.search.state.dest.length) {
            origin = FH.search.state.origin[0];
            destination = FH.search.state.dest[0];
          } else {
            FH.core.showError('Route "' + route + '" is not a 3-letter IATA pair. Run a search first or use + new watch.');
            return;
          }
          const today = new Date().toISOString().slice(0, 10);
          const horizon = new Date(Date.now() + 90 * 86400 * 1000).toISOString().slice(0, 10);
          try {
            await FH.core.api('/api/watchlist', {
              method: 'POST',
              body: JSON.stringify({
                origins: [origin], destinations: [destination],
                window_from: today,
                window_to:   horizon,
                max_usd: 500,
                cabin: 'Y'
              })
            });
            FH.core.go('watchlist');
          } catch (_) {
            // showError already fired by FH.core.api
          }
        });
      });
      wrap.querySelectorAll('button[data-act="copy"]').forEach(function (b) {
        b.addEventListener('click', function () {
          const card = b.closest('.fh-card');
          const route = card.querySelector('.fh-card__route').textContent.trim();
          // Defensive IATA parse — mistake routes can be tidy
          // ("MXP-ICN") or narrative ("United: San Francisco – Nashville
          // ..."). Only commit to FH.search.state when both halves are
          // valid 3-letter IATA codes; otherwise surface an error and
          // keep state untouched so the user doesn't land on /#search
          // with garbage chips.
          const iata = /^[A-Z]{3}$/;
          const parts = route.split('-').map(function (p) { return p.trim().toUpperCase(); });
          if (parts.length !== 2 || !iata.test(parts[0]) || !iata.test(parts[1])) {
            FH.core.showError('Route "' + route + '" is not a 3-letter IATA pair. Nothing copied.');
            return;
          }
          FH.search.state.origin = [parts[0]];
          FH.search.state.dest   = [parts[1]];
          // The bindChips render() closure is private to init(); call
          // the public re-painter so the user sees what was copied.
          FH.search.paintChips('origin');
          FH.search.paintChips('dest');
          FH.core.go('search');
        });
      });
    }
  };

  // ============================================================ //
  // WATCHLIST                                                    //
  // ============================================================ //

  FH.watchlist = {
    init: function () {
      document.getElementById('fh-watch-new').addEventListener('click', function () {
        document.getElementById('fh-watch-drawer').hidden = false;
      });
      document.getElementById('fh-watch-cancel').addEventListener('click', function () {
        document.getElementById('fh-watch-drawer').hidden = true;
      });
      document.getElementById('fh-watch-save').addEventListener('click', FH.watchlist.save);
    },
    refresh: async function () {
      try {
        const data = await FH.core.api('/api/watchlist');
        FH.watchlist.render(data.watches || []);
      } catch (e) {}
    },
    render: function (rows) {
      const tb = document.getElementById('fh-watch-tbody');
      if (!rows.length) {
        tb.innerHTML = '<tr class="fh-empty"><td colspan="8">'
          + '<div class="fh-empty__glyph">◯</div>'
          + '<div class="fh-empty__text">No watches. Click + new watch.</div>'
          + '</td></tr>';
        FH.core.footer('No watches @ ' + new Date().toLocaleTimeString(), 0);
        // Sidebar badges may be stale after a refresh; recompute.
        try { FH.core.updateBadges && FH.core.updateBadges(); } catch (_) {}
        return;
      }
      // Compute per-row freshness: ok if last_check < 12h, warn 12-48h,
      // stale otherwise; paused always = stale (visually distinct).
      const NOW = Date.now();
      function freshness(w) {
        if (w.paused) return 'stale';
        const ts = w.last_check;
        if (!ts) return 'stale';
        let t = 0;
        try { t = new Date(String(ts).replace(' ', 'T')).getTime() || 0; } catch (_) { t = 0; }
        if (!t) return 'stale';
        const h = (NOW - t) / 3600000;
        if (h < 12) return 'ok';
        if (h < 48) return 'warn';
        return 'stale';
      }
      tb.innerHTML = rows.map(function (w) {
        const window_str = (w.window_from || '?') + ' → ' + (w.window_to || '?');
        // Pause/Resume label + data-paused mirror: handler reads the data
        // attribute (current server state) rather than textContent, so a
        // future CSS text-transform can't silently invert the toggle.
        const paused = !!w.paused;
        const fr = freshness(w);
        const dot = '<span class="fh-dot fh-dot--' + fr + '" title="' +
          (fr === 'ok' ? 'Fresh (< 12h)' : fr === 'warn' ? 'Stale (12-48h)' : 'Cold / paused') +
          '"></span>';
        return ''
          + '<tr data-id="' + FH.core.escape(w.id) + '">'
          + '<td><span class="fh-route">' + FH.core.escape(w.origin) + ' <span class="fh-route__arrow">→</span> ' + FH.core.escape(w.destination) + '</span></td>'
          + '<td>' + FH.core.escape(window_str) + '</td>'
          + '<td class="fh-num">' + FH.core.fmtMoney(w.max_usd) + '</td>'
          + '<td>' + FH.core.escape(w.cabin || '-') + '</td>'
          + '<td>' + dot + ' ' + FH.core.fmtDate(w.last_check) + (paused ? ' <span class="fh-muted">(paused)</span>' : '') + '</td>'
          + '<td class="fh-num">' + (w.best_found_usd ? FH.core.fmtMoney(w.best_found_usd) : '-') + '</td>'
          + '<td class="fh-num">' + (w.alerts || 0) + '</td>'
          + '<td>'
          +   '<button class="fh-btn fh-btn--mini" data-act="pause"'
          +     ' data-id="' + FH.core.escape(w.id) + '"'
          +     ' data-paused="' + (paused ? '1' : '0') + '">'
          +     (paused ? 'Resume' : 'Pause')
          +   '</button> '
          +   '<button class="fh-btn fh-btn--mini fh-btn--danger" data-act="del" data-id="' + FH.core.escape(w.id) + '">Delete</button>'
          + '</td>'
          + '</tr>';
      }).join('');

      tb.querySelectorAll('button[data-act="pause"]').forEach(function (b) {
        b.addEventListener('click', async function (e) {
          e.stopPropagation();
          // Drive the toggle off data-paused (last known server state), not
          // visible button text, so CSS transforms can't break this.
          const currentlyPaused = b.dataset.paused === '1';
          try {
            await FH.core.api('/api/watchlist/' + encodeURIComponent(b.dataset.id), {
              method: 'PATCH',
              body: JSON.stringify({ paused: !currentlyPaused })
            });
            FH.watchlist.refresh();
          } catch (_) { /* showError already fired */ }
        });
      });
      tb.querySelectorAll('button[data-act="del"]').forEach(function (b) {
        b.addEventListener('click', async function (e) {
          e.stopPropagation();
          // Lightweight confirm — DELETE is destructive and the table has no
          // undo. window.confirm fall-through covers embedded WebViews that
          // strip the dialog.
          if (typeof window.confirm === 'function') {
            if (!window.confirm('Delete this watch? This cannot be undone.')) return;
          }
          try {
            await FH.core.api('/api/watchlist/' + encodeURIComponent(b.dataset.id), {
              method: 'DELETE'
            });
            FH.watchlist.refresh();
          } catch (_) { /* showError already fired */ }
        });
      });
      // Push watchlist alert count to sidebar badge (rows with alerts > 0).
      try {
        const alertCount = rows.reduce(function (n, w) { return n + (w.alerts > 0 ? 1 : 0); }, 0);
        FH.core.setBadge && FH.core.setBadge('watchlist', alertCount);
      } catch (_) {}
      FH.core.footer('Watches loaded @ ' + new Date().toLocaleTimeString(), rows.length);
    },
    save: async function () {
      const body = {
        origin:      (document.getElementById('fh-watch-origin').value || '').toUpperCase(),
        destination: (document.getElementById('fh-watch-dest').value   || '').toUpperCase(),
        window_from: document.getElementById('fh-watch-from').value || null,
        window_to:   document.getElementById('fh-watch-to').value   || null,
        max_usd:     parseInt(document.getElementById('fh-watch-max').value, 10) || 0,
        cabin:       document.getElementById('fh-watch-cabin').value
      };
      if (!body.origin || !body.destination) {
        FH.core.showError('Origin and destination required.');
        return;
      }
      try {
        await FH.core.api('/api/watchlist', { method: 'POST', body: JSON.stringify(body) });
      } catch (_) {
        // showError already fired — keep the drawer open so the user can
        // fix the input rather than losing what they typed.
        return;
      }
      document.getElementById('fh-watch-drawer').hidden = true;
      FH.core.toast('Watch saved — ' + body.origin + ' → ' + body.destination, 'success');
      FH.watchlist.refresh();
    }
  };

  // ============================================================ //
  // SWEET SPOTS                                                  //
  // ============================================================ //

  FH.spots = {
    loaded: false,
    raw: [],
    partners: {},
    view: [],

    init: function () {
      const inp = document.getElementById('fh-spots-search');
      inp.addEventListener('input', function () {
        const q = inp.value.toLowerCase();
        FH.spots.view = FH.spots.raw.filter(function (s) {
          // Prefer the server's `_search` blob (program + route + cabin +
          // notes + title + operating_carrier + status + region synonyms
          // like "japan" → Tokyo/NRT). Fall back to the visible-field
          // join when older payloads don't ship _search.
          const hay = (typeof s._search === 'string' && s._search)
            ? s._search
            : ((s.program || '') + ' ' + (s.route || '') + ' '
                + (s.cabin || '') + ' ' + (s.notes || '')).toLowerCase();
          return hay.indexOf(q) >= 0;
        });
        FH.spots.renderRows(FH.spots.view);
      });
      FH.core.sortTable('fh-spots-table', function () { return FH.spots.view; }, function (rows) {
        FH.spots.view = rows;
        FH.spots.renderRows(rows);
      });
    },

    // Render the sweet-spot status as a colored badge. The data uses
    // four buckets — "active" / "limited" / "devalued" / "GRAY" — but
    // riskBadge() only knows LEGAL/GRAY/TOS-RISK. Preserve the literal
    // status label (so the row says Active/Limited/Devalued, not a
    // homogenized "LEGAL"/"GRAY") and pick the CSS class by bucket:
    //   active   → fh-risk--legal  (sweet spot still works)
    //   limited  → fh-risk--gray   (intermittent availability)
    //   devalued → fh-risk--gray   (chart bumped — still bookable but worse)
    //   GRAY     → fh-risk--gray   (explicit policy-edge spot)
    // Output is emitted in Title Case; .fh-risk CSS applies
    // text-transform:uppercase, so the rendered pill still reads ACTIVE.
    statusBadge: function (status) {
      if (status === undefined || status === null || status === '') {
        return '<span class="fh-risk">-</span>';
      }
      const s = String(status);
      const u = s.toUpperCase();
      const cls = (u === 'ACTIVE') ? 'fh-risk--legal' : 'fh-risk--gray';
      const label = s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
      return '<span class="fh-risk ' + cls + '">'
        + FH.core.escape(label) + '</span>';
    },

    // Cabin labels in the dataset are lowercase enums ("first", "business",
    // "economy", "any"). Promote to Title Case for display while keeping
    // sort/filter behavior working off the raw value (the column sorts
    // alphabetically on the cell text — title-casing every row preserves
    // ordering since first-letter casing is uniform).
    cabinLabel: function (cabin) {
      const c = (cabin == null ? '' : String(cabin)).trim();
      if (!c) return '-';
      return c.charAt(0).toUpperCase() + c.slice(1).toLowerCase();
    },

    loadOnce: async function () {
      if (FH.spots.loaded) return;
      try {
        const data = await FH.core.api('/api/sweet-spots');
        FH.spots.raw      = data.sweet_spots || [];
        FH.spots.partners = data.transfer_partners || {};
        FH.spots.view     = FH.spots.raw.slice();
        FH.spots.renderRows(FH.spots.view);
        FH.spots.loaded = true;
      } catch (e) {}
    },

    renderRows: function (rows) {
      const tb = document.getElementById('fh-spots-tbody');
      if (!rows.length) {
        tb.innerHTML = '<tr class="fh-empty"><td colspan="6">'
          + '<div class="fh-empty__glyph">◇</div>'
          + '<div class="fh-empty__text">No spots match filter.</div>'
          + '</td></tr>';
        return;
      }
      tb.innerHTML = rows.map(function (s, i) {
        const miles = (typeof s.miles === 'number' && s.miles > 0) ? s.miles.toLocaleString() : '-';
        const logoTag = s.program_logo_url
          ? '<img class="fh-carrier__logo" src="' + FH.core.escape(s.program_logo_url) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'"/>'
          : '';
        const progCell = '<span class="fh-carrier">' + logoTag + '<span class="fh-carrier__name">' + FH.core.escape(s.program) + '</span></span>';
        return ''
          + '<tr data-i="' + i + '">'
          + '<td>' + progCell + '</td>'
          + '<td>' + FH.core.escape(s.route) + '</td>'
          + '<td>' + FH.core.escape(FH.spots.cabinLabel(s.cabin)) + '</td>'
          + '<td class="fh-num">' + miles + '</td>'
          + '<td>' + FH.core.escape(s.notes) + '</td>'
          + '<td>' + FH.spots.statusBadge(s.status) + '</td>'
          + '</tr>';
      }).join('');
      tb.querySelectorAll('tr[data-i]').forEach(function (tr) {
        tr.addEventListener('click', function (e) {
          const tgt = e.target;
          if (tgt && tgt.closest && tgt.closest('a,button')) return;
          const s = rows[parseInt(tr.dataset.i, 10)];
          if (!s) return;
          FH.core.setActiveRow(tr);
          FH.spots.openOverlay(s);
        });
      });
      FH.core.footer('Sweet spots loaded', rows.length);
    },

    // Open the row overlay. Show static program/route/cabin/miles/notes/
    // status up front, then asynchronously enrich the TRANSFER PATHS section
    // with the user-balance-aware /api/sweet-spots/{id}/transfers endpoint.
    // Falls back to the generic partner list (FH.spots.partners) so the
    // overlay is always useful — even if the spot has no id or the
    // transfers endpoint errors.
    openOverlay: function (s) {
      const miles = (typeof s.miles === 'number' && s.miles > 0)
                  ? s.miles.toLocaleString()
                  : '-';
      const partners = FH.spots.partners[s.program] || [];
      const partnerList = partners.length
        ? partners.map(function (p) {
            return '<pre>' + FH.core.escape(p.card.padEnd(24) + ' → ' + s.program + '  (' + p.ratio + ')') + '</pre>';
          }).join('')
        : '<pre>No direct transfer partners on file.</pre>';
      const html = ''
        + '<div class="fh-kv">'
        +   '<div class="fh-kv__k">Program</div><div class="fh-kv__v">' + FH.core.escape(s.program) + '</div>'
        +   '<div class="fh-kv__k">Route</div>  <div class="fh-kv__v">' + FH.core.escape(s.route) + '</div>'
        +   '<div class="fh-kv__k">Cabin</div>  <div class="fh-kv__v">' + FH.core.escape(FH.spots.cabinLabel(s.cabin)) + '</div>'
        +   '<div class="fh-kv__k">Miles</div>  <div class="fh-kv__v">' + miles + '</div>'
        +   '<div class="fh-kv__k">Notes</div>  <div class="fh-kv__v">' + FH.core.escape(s.notes) + '</div>'
        +   '<div class="fh-kv__k">Status</div> <div class="fh-kv__v">' + FH.spots.statusBadge(s.status) + '</div>'
        + '</div>'
        + '<h3>Transfer Paths</h3>'
        + '<div id="fh-spots-paths">' + partnerList + '</div>';
      FH.core.openOverlay('Sweet spot — ' + s.program + ' / ' + s.route, html);

      // Async enrichment: replace the generic partner list with reachable
      // paths from the user's actual balances (server applies the user's
      // transfer ratios + effective balance). Skip when the row has no id.
      if (!s.id) return;
      (async function () {
        try {
          const data = await FH.core.api('/api/sweet-spots/' + encodeURIComponent(s.id) + '/transfers');
          const paths = (data && data.paths) || [];
          const eff   = (data && data.effective_balance_for_program) || 0;
          const need  = (data && data.miles_needed) || s.miles || 0;
          const host  = document.getElementById('fh-spots-paths');
          if (!host) return;
          if (!paths.length) {
            host.innerHTML = '<pre>No reachable paths from your current balances.</pre>';
            return;
          }
          // Server path shape (server.py _transfer_paths):
          //   type: "direct" | "transfer"
          //   currency: e.g. "Amex Membership Rewards" (transfer source)
          //   card_balance: raw card-currency balance
          //   available_miles: miles after transfer (or direct balance)
          //   miles_needed, covers, ratio, via_program, min_transfer, transfer_time
          // Direct paths set program/balance instead of currency/card_balance,
          // so fall back gracefully across both shapes.
          host.innerHTML = paths.map(function (p) {
            const from   = p.currency || p.program || p.source_currency || p.from || '?';
            const ratio  = p.ratio || '1:1';
            const bal    = (typeof p.card_balance === 'number')   ? p.card_balance.toLocaleString()
                          : (typeof p.balance === 'number')       ? p.balance.toLocaleString()
                          : '-';
            const after  = (typeof p.available_miles === 'number') ? p.available_miles.toLocaleString()
                          : (typeof p.miles_after_transfer === 'number') ? p.miles_after_transfer.toLocaleString()
                          : '-';
            const covers = p.covers ? ' COVERS' : '';
            return '<pre>' + FH.core.escape(
                String(from).padEnd(28)
                + ' -> ' + s.program
                + '  (' + ratio + ')  bal=' + bal
                + '  -> ' + after + ' mi' + covers
              ) + '</pre>';
          }).join('')
          + '<div class="fh-hint">Effective balance for this program: '
          + eff.toLocaleString() + ' / ' + need.toLocaleString() + ' miles needed.</div>';
        } catch (e) { /* keep the generic partner list on error */ }
      })();
    }
  };

  // ============================================================ //
  // BALANCES                                                     //
  // ============================================================ //

  FH.balances = {
    // Canonical UI-side state keeps ONLY the 5 abbreviation keys.
    // The server happily round-trips both abbrev and full-name shapes,
    // but mixing both in state caused user edits to lose to stale
    // full-name copies on save (server's _expand_currency_abbrevs picks
    // full names over abbreviations when both are present).
    state: {
      currencies: { UR: 0, MR: 0, TY: 0, VENTURE: 0, BILT: 0 },
      airlines: []
    },
    partners: {},
    loaded: false,
    _bound: false,

    // Map full credit-card-currency names (as returned by /api/sweet-spots
    // in transfer_partners[program][i].card) back to the UI-side
    // abbreviation we use as state.currencies keys.
    CARD_TO_ABBREV: {
      'Chase Ultimate Rewards':              'UR',
      'Amex Membership Rewards':             'MR',
      'American Express Membership Rewards': 'MR',
      'Citi ThankYou':                       'TY',
      'Citi ThankYou Rewards':               'TY',
      'Capital One Miles':                   'VENTURE',
      'Capital One Venture':                 'VENTURE',
      'Bilt Rewards':                        'BILT',
      'Marriott Bonvoy':                     'BONVOY'
    },

    init: function () {
      document.getElementById('fh-bal-add').addEventListener('click', function () {
        FH.balances.state.airlines.push({ program: '', miles: 0 });
        FH.balances.renderTable();
      });
      document.getElementById('fh-balances-form').addEventListener('submit', function (e) {
        e.preventDefault();
        FH.balances.save();
      });
      // Bind currency input listeners ONCE — the inputs are static HTML,
      // so re-binding on every navigation to /#balances would stack
      // duplicate handlers and make a single keystroke fire N times.
      FH.balances.bindCurrencyInputs();
    },

    bindCurrencyInputs: function () {
      if (FH.balances._bound) return;
      FH.balances._bound = true;
      document.querySelectorAll('[data-currency]').forEach(function (el) {
        el.addEventListener('input', function () {
          const key = el.dataset.currency;
          FH.balances.state.currencies[key] = parseInt(el.value, 10) || 0;
          // Currency changed → every airline row's reachable/effective
          // needs to redraw. Currency inputs sit OUTSIDE the airline
          // <tbody>, so re-rendering doesn't steal focus from the user.
          FH.balances.renderTable();
        });
      });
    },

    loadOnce: async function () {
      if (FH.balances.loaded) return;
      try {
        const sp = await FH.core.api('/api/sweet-spots');
        FH.balances.partners = sp.transfer_partners || {};
      } catch (e) {}
      try {
        const data = await FH.core.api('/api/balances');
        const b = data.balances || {};
        const srvCur = b.currencies || {};
        // Reset state to abbrev-only — pick the 5 abbrev values from the
        // server response. Never carry full-name keys in state, or the
        // POST round-trip will let stale full-name copies overwrite user
        // edits (server-side _expand_currency_abbrevs: "full name wins").
        const cur = { UR: 0, MR: 0, TY: 0, VENTURE: 0, BILT: 0 };
        Object.keys(cur).forEach(function (k) {
          if (typeof srvCur[k] === 'number') cur[k] = srvCur[k];
        });
        FH.balances.state = {
          currencies: cur,
          airlines: Array.isArray(b.airlines) ? b.airlines.map(function (a) {
            return {
              program: a.program || '',
              miles: parseInt(a.miles != null ? a.miles : a.balance, 10) || 0
            };
          }) : []
        };
        // Fall back to localStorage ONLY when the server confirms this is a
        // first-time user (data.source points at the example file — meaning
        // user_balances.json doesn't exist yet on disk). Previously this also
        // triggered when user_balances.json existed but contained explicit
        // zeros, which let stale localStorage values from a previous session
        // resurrect AFTER the user had intentionally saved zeros. That looked
        // exactly like "balances aren't persisting" — save zeros, reload,
        // see old non-zero values.
        const isFirstTimeUser = (typeof data.source === 'string') &&
                                data.source.indexOf('.example.') !== -1;
        if (isFirstTimeUser) {
          const ls = localStorage.getItem('fh.balances');
          if (ls) {
            try {
              const lo = JSON.parse(ls);
              if (lo && lo.currencies) {
                Object.keys(cur).forEach(function (k) {
                  if (typeof lo.currencies[k] === 'number') FH.balances.state.currencies[k] = lo.currencies[k];
                });
              }
              if (lo && Array.isArray(lo.airlines)) FH.balances.state.airlines = lo.airlines;
            } catch (_) {}
          }
        }
      } catch (e) {}
      FH.balances.loaded = true;
      FH.balances.renderCurrencies();
      FH.balances.renderTable();
    },

    renderCurrencies: function () {
      // Value-set only; listeners are bound once in init() via bindCurrencyInputs.
      Object.keys(FH.balances.state.currencies).forEach(function (k) {
        const el = document.querySelector('[data-currency="' + k + '"]');
        if (el) el.value = FH.balances.state.currencies[k] || 0;
      });
    },

    reachable: function (program) {
      const partners = FH.balances.partners[program] || [];
      let total = 0;
      partners.forEach(function (p) {
        // p.card is the full credit-card-currency name (e.g. "Chase
        // Ultimate Rewards"). Translate to the UI abbrev before lookup;
        // fall back to a direct lookup so unknown card names still work
        // when state happens to carry that key.
        const abbrev = FH.balances.CARD_TO_ABBREV[p.card];
        const bal = (abbrev && FH.balances.state.currencies[abbrev])
                 || FH.balances.state.currencies[p.card]
                 || 0;
        const parts = (p.ratio || '1:1').split(':');
        const lhs = parseFloat(parts[0]) || 1;
        const rhs = parseFloat(parts[1]) || 1;
        total += Math.floor(bal * (rhs / lhs));
      });
      return total;
    },

    renderTable: function () {
      const tb = document.getElementById('fh-bal-tbody');
      if (!FH.balances.state.airlines.length) {
        tb.innerHTML = '<tr class="fh-empty"><td colspan="5">No airline programs yet. Click + Add Program.</td></tr>';
        return;
      }
      tb.innerHTML = FH.balances.state.airlines.map(function (a, i) {
        const reachable = FH.balances.reachable(a.program);
        const effective = (parseInt(a.miles, 10) || 0) + reachable;
        return ''
          + '<tr data-row="' + i + '">'
          + '<td><input type="text"   class="fh-input" data-i="' + i + '" data-k="program" value="' + FH.core.escape(a.program) + '" placeholder="Program name (e.g. ANA Mileage Club)" /></td>'
          + '<td class="fh-num"><input type="number" class="fh-input fh-input--num" data-i="' + i + '" data-k="miles"   value="' + (a.miles || 0) + '" min="0" /></td>'
          + '<td class="fh-num"               data-cell="reachable">' + (reachable ? reachable.toLocaleString() : '0') + '</td>'
          + '<td class="fh-num fh-mono-em"    data-cell="effective">' + effective.toLocaleString() + '</td>'
          + '<td class="fh-cell-actions"><button type="button" class="fh-btn fh-btn--mini fh-btn--danger" data-del="' + i + '">Remove</button></td>'
          + '</tr>';
      }).join('');

      tb.querySelectorAll('input').forEach(function (inp) {
        inp.addEventListener('input', function () {
          const i = parseInt(inp.dataset.i, 10);
          const k = inp.dataset.k;
          if (k === 'program') {
            // Store the raw string; don't force-uppercase mid-keystroke
            // (breaks IME and is jarring in the input field). The save
            // path strips/trims; the server stores the program name
            // verbatim so user-controlled casing is fine.
            FH.balances.state.airlines[i][k] = inp.value;
          } else {
            FH.balances.state.airlines[i][k] = parseInt(inp.value, 10) || 0;
          }
          // Update only the affected row's reachable+effective cells
          // in-place. Re-rendering the whole table on every keystroke
          // (or even on every blur, the old `change` listener) ripped
          // focus out of the next field the user was tabbing into.
          const row = tb.querySelector('tr[data-row="' + i + '"]');
          if (!row) return;
          const a = FH.balances.state.airlines[i];
          const r = FH.balances.reachable(a.program);
          const eff = (parseInt(a.miles, 10) || 0) + r;
          const rc = row.querySelector('[data-cell="reachable"]');
          const ec = row.querySelector('[data-cell="effective"]');
          if (rc) rc.textContent = r ? r.toLocaleString() : '0';
          if (ec) ec.textContent = eff.toLocaleString();
        });
      });
      tb.querySelectorAll('button[data-del]').forEach(function (b) {
        b.addEventListener('click', function () {
          FH.balances.state.airlines.splice(parseInt(b.dataset.del, 10), 1);
          FH.balances.renderTable();
        });
      });
    },

    save: async function () {
      // BEFORE building the body, force-sync state from any currently-focused
      // currency input. An `input` event fires per keystroke synchronously,
      // but defending against the edge case where the click handler races the
      // last keystroke (e.g. IME composition, programmatic input) costs us
      // nothing and guarantees the POST body reflects exactly what's on
      // screen — which is what the user means by "balances should persist
      // when hitting save".
      document.querySelectorAll('[data-currency]').forEach(function (el) {
        const k = el.dataset.currency;
        if (k in FH.balances.state.currencies) {
          FH.balances.state.currencies[k] = parseInt(el.value, 10) || 0;
        }
      });
      // Same belt-and-suspenders for the airline-row inputs — pull the
      // live DOM value into state right before serializing.
      const tb = document.getElementById('fh-bal-tbody');
      if (tb) {
        tb.querySelectorAll('input[data-i]').forEach(function (inp) {
          const i = parseInt(inp.dataset.i, 10);
          const k = inp.dataset.k;
          if (!FH.balances.state.airlines[i]) return;
          if (k === 'program') {
            FH.balances.state.airlines[i][k] = inp.value;
          } else if (k === 'miles') {
            FH.balances.state.airlines[i][k] = parseInt(inp.value, 10) || 0;
          }
        });
      }

      // Build the POST body fresh from state. State carries ONLY abbrev
      // currency keys, so the server's _expand_currency_abbrevs won't
      // see stale full-name copies that would silently win over edits.
      // Empty-program rows are filtered out so we don't ship junk.
      const body = {
        currencies: Object.assign({}, FH.balances.state.currencies),
        airlines: FH.balances.state.airlines
          .filter(function (a) { return (a.program || '').trim().length > 0; })
          .map(function (a) {
            return { program: (a.program || '').trim(), miles: parseInt(a.miles, 10) || 0 };
          })
      };
      try {
        const resp = await FH.core.api('/api/balances', {
          method: 'POST',
          body: JSON.stringify(body)
        });
        // CRITICAL: rebuild state from the server's response so the UI
        // shows exactly what landed (after trimming, empty-row filtering,
        // and abbrev↔full-name canonicalization). Without this, a second
        // save could re-POST whatever stale shape was in state from the
        // user's mid-edit keystrokes, and the user would see a flicker
        // between what they typed and what persisted on the next reload.
        const sb = (resp && resp.balances) || {};
        const srvCur = sb.currencies || {};
        const cur = { UR: 0, MR: 0, TY: 0, VENTURE: 0, BILT: 0 };
        Object.keys(cur).forEach(function (k) {
          if (typeof srvCur[k] === 'number') cur[k] = srvCur[k];
        });
        FH.balances.state = {
          currencies: cur,
          airlines: Array.isArray(sb.airlines) ? sb.airlines.map(function (a) {
            return {
              program: a.program || '',
              miles: parseInt(a.miles != null ? a.miles : a.balance, 10) || 0
            };
          }) : []
        };
        FH.balances.renderCurrencies();
        FH.balances.renderTable();
        localStorage.setItem('fh.balances', JSON.stringify(body));
        document.getElementById('fh-bal-status').textContent = 'Saved @ ' + new Date().toLocaleTimeString();
        FH.core.toast('Balances saved (' + body.airlines.length + ' program' + (body.airlines.length === 1 ? '' : 's') + ')', 'success');
        // Force next /#balances visit to re-fetch from disk so the user
        // sees exactly what landed (including server-side filtering of
        // empty-program rows).
        FH.balances.loaded = false;
      } catch (e) {
        // backend missing; persist locally
        localStorage.setItem('fh.balances', JSON.stringify(body));
        document.getElementById('fh-bal-status').textContent = 'Saved locally (no backend).';
        FH.core.toast('Balances saved locally (no backend)', 'warn');
      }
    }
  };

  // ============================================================ //
  // SETTINGS                                                     //
  // ============================================================ //

  FH.settings = {
    loadOnce: async function () {
      let s = null;
      try {
        const data = await FH.core.api('/api/settings');
        s = data.settings || null;
      } catch (e) {}
      if (!s) {
        const ls = localStorage.getItem('fh.settings');
        if (ls) try { s = JSON.parse(ls); } catch (_) {}
      }
      if (!s) s = { seats_aero_key: '', telegram_webhook: '', cpp_source: 'avg', cache_ttl: 3600 };
      document.getElementById('fh-set-seats').value = s.seats_aero_key || '';
      document.getElementById('fh-set-tg').value    = s.telegram_webhook || '';
      document.getElementById('fh-set-ttl').value   = s.cache_ttl || 3600;
      const r = document.querySelector('input[name=cpp][value="' + (s.cpp_source || 'avg') + '"]');
      if (r) r.checked = true;
    },

    init: function () {
      document.getElementById('fh-settings-form').addEventListener('submit', function (e) {
        e.preventDefault();
        FH.settings.save();
      });
      document.getElementById('fh-set-refresh').addEventListener('click', FH.settings.refresh);
    },

    collect: function () {
      const r = document.querySelector('input[name=cpp]:checked');
      return {
        seats_aero_key:    document.getElementById('fh-set-seats').value,
        telegram_webhook:  document.getElementById('fh-set-tg').value,
        cpp_source:        (r && r.value) || 'avg',
        cache_ttl:         parseInt(document.getElementById('fh-set-ttl').value, 10) || 3600
      };
    },

    save: async function () {
      const body = FH.settings.collect();
      try {
        const resp = await FH.core.api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
        // Persist locally only AFTER server accepts — and use the *masked*
        // response so we don't cache the real key into localStorage.
        const cache = Object.assign({}, body, (resp && resp.settings) || {});
        localStorage.setItem('fh.settings', JSON.stringify(cache));
        document.getElementById('fh-set-status').textContent = 'Saved @ ' + new Date().toLocaleTimeString();
        FH.core.toast('Settings saved', 'success');
        // Re-hydrate the form so the user sees the freshly-masked key
        // (server may have rotated the mask if the value was just set).
        if (resp && resp.settings) {
          const sset = resp.settings;
          document.getElementById('fh-set-seats').value = sset.seats_aero_key || '';
          document.getElementById('fh-set-tg').value    = sset.telegram_webhook || '';
          if (typeof sset.cache_ttl === 'number') {
            document.getElementById('fh-set-ttl').value = sset.cache_ttl;
          }
          const rr = document.querySelector('input[name=cpp][value="' + (sset.cpp_source || 'avg') + '"]');
          if (rr) rr.checked = true;
        }
      } catch (e) {
        // backend missing; persist locally — but DON'T persist the raw key
        // into localStorage either. Strip secrets to a length-only marker.
        const safe = Object.assign({}, body, {
          seats_aero_key:   body.seats_aero_key ? '****' : '',
          telegram_webhook: body.telegram_webhook ? '****' : ''
        });
        localStorage.setItem('fh.settings', JSON.stringify(safe));
        document.getElementById('fh-set-status').textContent = 'Saved locally (no backend).';
      }
    },

    refresh: async function () {
      document.getElementById('fh-set-status').textContent = 'Refreshing data...';
      try {
        const data = await FH.core.api('/api/refresh', { method: 'POST', body: '{}' });
        document.getElementById('fh-set-status').textContent = 'Refreshed @ ' + FH.core.fmtDate(data.refreshed_at);
      } catch (e) {
        document.getElementById('fh-set-status').textContent = 'Refresh failed.';
      }
    }
  };

  // ============================================================ //
  // FLEX DATES                                                   //
  // ============================================================ //

  FH.flex = {
    state: { origin: [], dest: [] },
    lastDays: [],
    lastMeta: {},

    init: function () {
      FH.flex.bindChips('origin');
      FH.flex.bindChips('dest');

      const form = document.getElementById('fh-flex-form');
      if (form) {
        form.addEventListener('submit', function (e) {
          e.preventDefault();
          FH.flex.submit();
        });
      }
      const reset = document.getElementById('fh-flex-reset');
      if (reset) reset.addEventListener('click', FH.flex.reset);

      // Default the window to next month, days 1..14
      const start = document.getElementById('fh-flex-start');
      const end   = document.getElementById('fh-flex-end');
      if (start && !start.value && end && !end.value) {
        const today = new Date();
        const s = new Date(today.getFullYear(), today.getMonth() + 1, 1);
        const e = new Date(s.getFullYear(), s.getMonth(), 14);
        const fmt = function (d) {
          const m = d.getMonth() + 1, dd = d.getDate();
          return d.getFullYear() + '-' + (m < 10 ? '0' + m : m) + '-' + (dd < 10 ? '0' + dd : dd);
        };
        start.value = fmt(s);
        end.value   = fmt(e);
      }
    },

    // Mirror of FH.search.bindChips bound to flex-namespaced DOM ids.
    // Flex mode keeps a single origin and a single destination (replace, not append).
    bindChips: function (kind) {
      const input = document.getElementById('fh-flex-' + kind + '-input');
      const list  = document.getElementById('fh-flex-suggest-' + kind);
      if (!input || !list) return;

      let active = -1;
      let cache  = [];
      let reqSeq = 0;
      let debounceTimer = null;
      const DEBOUNCE_MS = 80;

      function repaint() {
        const chips = document.getElementById('fh-flex-chips-' + kind);
        if (!chips) return;
        chips.innerHTML = '';
        FH.flex.state[kind].forEach(function (iata) {
          const chip = document.createElement('span');
          chip.className = 'fh-chip';
          chip.innerHTML = FH.core.escape(iata) + ' <span class="fh-chip__x">x</span>';
          chip.querySelector('.fh-chip__x').addEventListener('click', function () {
            FH.flex.state[kind] = FH.flex.state[kind].filter(function (c) { return c !== iata; });
            repaint();
          });
          chips.appendChild(chip);
        });
      }
      repaint();

      function close() { list.hidden = true; active = -1; }
      function add(iata) {
        iata = (iata || '').trim().toUpperCase();
        if (!iata) return;
        FH.flex.state[kind] = [iata];   // single-slot
        input.value = '';
        close();
        repaint();
      }

      async function doFetch(q, mySeq) {
        try {
          const data = await fetch('/api/hubs?q=' + encodeURIComponent(q))
            .then(function (r) { return r.json(); });
          if (mySeq !== reqSeq) return;
          if (input.value.trim() !== q) return;
          cache = data.hubs || [];
          if (!cache.length) { close(); return; }
          list.innerHTML = cache.map(function (h, i) {
            const code = FH.core.escape(h.iata);
            return '<li data-iata="' + code + '" data-i="' + i + '">'
              + '<span class="fh-suggest__code">' + code + '</span>'
              + '<span class="fh-suggest__name">' + FH.core.escape(h.city)
              + ' (' + FH.core.escape(h.country) + ')</span>'
              + '</li>';
          }).join('');
          list.hidden = false;
          active = -1;
          list.querySelectorAll('li').forEach(function (li) {
            li.addEventListener('mousedown', function (e) {
              e.preventDefault();
              add(li.dataset.iata);
            });
          });
        } catch (e) {
          if (mySeq === reqSeq) close();
        }
      }

      input.addEventListener('input', function () {
        const q = input.value.trim();
        reqSeq += 1;
        const mySeq = reqSeq;
        if (debounceTimer) clearTimeout(debounceTimer);
        if (!q) { close(); return; }
        debounceTimer = setTimeout(function () { doFetch(q, mySeq); }, DEBOUNCE_MS);
      });

      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          if (active >= 0 && cache[active]) add(cache[active].iata);
          else if (input.value) add(input.value);
          return;
        }
        if (e.key === 'Backspace' && !input.value && FH.flex.state[kind].length) {
          FH.flex.state[kind].pop();
          repaint();
          return;
        }
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (!cache.length) return;
          active = (active + 1) % cache.length;
          list.querySelectorAll('li').forEach(function (li, i) {
            li.classList.toggle('fh-suggest--active', i === active);
          });
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (!cache.length) return;
          active = (active - 1 + cache.length) % cache.length;
          list.querySelectorAll('li').forEach(function (li, i) {
            li.classList.toggle('fh-suggest--active', i === active);
          });
          return;
        }
        if (e.key === 'Escape') { close(); }
      });

      input.addEventListener('blur', function () { setTimeout(close, 120); });
    },

    collect: function () {
      const modeEl = document.querySelector('input[name=fh-flex-mode]:checked');
      return {
        origin:      (FH.flex.state.origin[0] || '').toUpperCase(),
        destination: (FH.flex.state.dest[0]   || '').toUpperCase(),
        start_date:  document.getElementById('fh-flex-start').value || '',
        end_date:    document.getElementById('fh-flex-end').value   || '',
        cabin:       document.getElementById('fh-flex-cabin').value || 'Y',
        adults:      Math.max(1, parseInt(document.getElementById('fh-flex-adults').value || '1', 10) || 1),
        mode:        (modeEl && modeEl.value) || 'cash'
      };
    },

    submit: async function () {
      const q = FH.flex.collect();
      if (!q.origin || !q.destination) {
        FH.core.showError('Pick a single origin and a single destination.');
        return;
      }
      if (q.origin === q.destination) {
        FH.core.showError('Origin and destination must differ.');
        return;
      }
      if (!q.start_date || !q.end_date) {
        FH.core.showError('Pick a window start and end date.');
        return;
      }
      if (q.end_date < q.start_date) {
        FH.core.showError('Window end must be on or after window start.');
        return;
      }

      const prog = document.getElementById('fh-flex-progress');
      const lbl  = document.getElementById('fh-flex-progress-label');
      prog.hidden = false;
      prog.style.display = '';
      const btn = document.getElementById('fh-flex-go');
      if (btn) { btn.disabled = true; btn.textContent = 'Scanning…'; }

      const dStart = new Date(q.start_date + 'T00:00:00Z');
      const dEnd   = new Date(q.end_date   + 'T00:00:00Z');
      const ndays  = Math.round((dEnd - dStart) / 86400000) + 1;

      const t0 = performance.now();
      const tick = function () {
        const sec = Math.floor((performance.now() - t0) / 1000);
        lbl.textContent = 'Scanning ' + ndays + ' day' + (ndays === 1 ? '' : 's') +
                          ' (' + q.origin + ' → ' + q.destination + ', ' + q.mode + ') — ' + sec + 's';
      };
      tick();
      const progressTimer = setInterval(tick, 500);

      document.getElementById('fh-flex-empty').hidden = true;

      try {
        const data = await FH.core.api('/api/calendar', {
          method: 'POST',
          body: JSON.stringify(q)
        });
        const ms = Math.round(performance.now() - t0);
        FH.flex.lastDays = data.days || [];
        FH.flex.lastMeta = data.meta || {};
        FH.flex.render(FH.flex.lastDays, q);
        const errCount = Object.keys(FH.flex.lastMeta.errors_per_day || {}).length;
        document.getElementById('fh-flex-footer').textContent =
          'Searched ' + (FH.flex.lastMeta.days_searched || FH.flex.lastDays.length) +
          ' day' + (FH.flex.lastDays.length === 1 ? '' : 's') +
          ' in ' + (ms / 1000).toFixed(1) + 's' +
          (FH.flex.lastMeta.cached ? ' (cached)' : '') +
          (errCount ? ' · ' + errCount + ' day' + (errCount === 1 ? '' : 's') + ' had errors' : '');
        FH.core.footer(
          'Calendar @ ' + new Date().toLocaleTimeString() + ' (' + ms + 'ms)',
          FH.flex.lastDays.length
        );
      } catch (e) {
        // showError already fired
      } finally {
        clearInterval(progressTimer);
        prog.hidden = true;
        prog.style.display = 'none';
        if (btn) { btn.disabled = false; btn.textContent = 'Search calendar'; }
      }
    },

    reset: function () {
      FH.flex.state.origin = [];
      FH.flex.state.dest   = [];
      document.getElementById('fh-flex-chips-origin').innerHTML = '';
      document.getElementById('fh-flex-chips-dest').innerHTML   = '';
      const form = document.getElementById('fh-flex-form');
      if (form) form.reset();
      const cabinSel = document.getElementById('fh-flex-cabin');
      if (cabinSel) cabinSel.value = 'Y';
      const modeRadio = document.querySelector('input[name=fh-flex-mode][value=cash]');
      if (modeRadio) modeRadio.checked = true;
      document.getElementById('fh-cal-wrap').hidden = true;
      document.getElementById('fh-flex-summary').hidden = true;
      document.getElementById('fh-flex-empty').hidden = false;
      document.getElementById('fh-flex-footer').textContent = '';
      FH.flex.lastDays = [];
      FH.flex.lastMeta = {};
    },

    // Compute quartile/percentile bands across the non-null day values.
    bands: function (values) {
      const nums = (values || []).filter(function (v) {
        return typeof v === 'number' && isFinite(v);
      }).slice().sort(function (a, b) { return a - b; });
      if (!nums.length) return null;
      const pct = function (p) {
        if (nums.length === 1) return nums[0];
        const idx = (nums.length - 1) * p;
        const lo = Math.floor(idx), hi = Math.ceil(idx);
        if (lo === hi) return nums[lo];
        return nums[lo] + (nums[hi] - nums[lo]) * (idx - lo);
      };
      return {
        min: nums[0],
        q1: pct(0.25),
        median: pct(0.5),
        q3: pct(0.75),
        p90: pct(0.90),
        max: nums[nums.length - 1],
        count: nums.length
      };
    },

    cellClass: function (value, stats) {
      if (value === null || value === undefined || !isFinite(value)) return 'fh-cal-cell--nodata';
      if (!stats) return '';
      if (value <= stats.q1)     return 'fh-cal-cell--cheap';
      if (value <= stats.median) return 'fh-cal-cell--mid-low';
      if (value <= stats.p90)    return 'fh-cal-cell--mid-high';
      return 'fh-cal-cell--expensive';
    },

    render: function (days, q) {
      const cal = document.getElementById('fh-calendar');
      const wrap = document.getElementById('fh-cal-wrap');
      const summary = document.getElementById('fh-flex-summary');
      const legend = document.getElementById('fh-cal-legend');
      const empty = document.getElementById('fh-flex-empty');
      if (!cal || !wrap) return;
      if (!days || !days.length) {
        wrap.hidden = true;
        summary.hidden = true;
        empty.hidden = false;
        return;
      }
      empty.hidden = true;

      // Rank by cash for cash/both modes, by miles for award mode.
      const mode = (q && q.mode) || 'cash';
      const valueOf = function (d) {
        if (mode === 'award') return d.cheapest_award_miles;
        return d.cheapest_cash_usd != null ? d.cheapest_cash_usd : null;
      };

      const stats = FH.flex.bands(days.map(valueOf));

      let summaryHtml = '';
      if (stats) {
        const minDay = days.find(function (d) { return valueOf(d) === stats.min; });
        const maxDay = days.find(function (d) { return valueOf(d) === stats.max; });
        const fmtVal = function (v) {
          if (v == null) return '—';
          return mode === 'award'
            ? Number(v).toLocaleString() + ' mi'
            : FH.core.fmtMoney(v);
        };
        const fmtDay = function (d) {
          if (!d || !d.date) return '—';
          const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(d.date);
          if (!m) return d.date;
          const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
          return months[parseInt(m[2], 10) - 1] + ' ' + parseInt(m[3], 10);
        };
        summaryHtml =
          '<span><span class="fh-flex-summary__lbl">Cheapest:</span>' +
            '<span class="fh-flex-summary__big">' + FH.core.escape(fmtVal(stats.min)) + '</span>' +
            ' on ' + FH.core.escape(fmtDay(minDay)) + '</span>' +
          '<span><span class="fh-flex-summary__lbl">Most expensive:</span>' +
            FH.core.escape(fmtVal(stats.max)) + ' on ' + FH.core.escape(fmtDay(maxDay)) + '</span>' +
          '<span><span class="fh-flex-summary__lbl">Median:</span>' +
            FH.core.escape(fmtVal(stats.median)) + '</span>' +
          '<span><span class="fh-flex-summary__lbl">Days with data:</span>' +
            stats.count + ' / ' + days.length + '</span>';
      } else {
        summaryHtml = '<span class="fh-text-mut">No fare data returned for any day in this window.</span>';
      }
      summary.innerHTML = summaryHtml;
      summary.hidden = false;

      // Prepend empty filler cells so the first day aligns with the correct DOW column.
      const first = new Date(days[0].date + 'T00:00:00Z');
      const startDow = first.getUTCDay();   // 0=Sun..6=Sat
      let html = '';
      for (let i = 0; i < startDow; i++) {
        html += '<div class="fh-cal-cell fh-cal-cell--filler"></div>';
      }
      // Pin the lowest-value day (single winner) so its cell can get the
      // subtle outline pulse via .fh-cal-cell--lowest. Ties resolve to the
      // earliest date in the window.
      const lowestVal = stats ? stats.min : null;
      let lowestIdx = -1;
      if (lowestVal != null) {
        days.forEach(function (d, i) {
          if (lowestIdx === -1 && valueOf(d) === lowestVal) lowestIdx = i;
        });
      }
      days.forEach(function (d, idx) {
        const val = valueOf(d);
        let cls = FH.flex.cellClass(val, stats);
        if (idx === lowestIdx) cls += ' fh-cal-cell--lowest';
        const dateNum = parseInt(d.date.slice(8, 10), 10);
        let priceHtml;
        if (val == null) {
          priceHtml = '<div class="fh-cal-cell__price">—</div>';
        } else if (mode === 'award') {
          priceHtml = '<div class="fh-cal-cell__price fh-cal-cell__price--miles">' +
                      Number(val).toLocaleString() + '</div>';
        } else {
          priceHtml = '<div class="fh-cal-cell__price">' + FH.core.fmtMoney(val) + '</div>';
        }
        const metaParts = [];
        if (d.sample_carrier) metaParts.push(d.sample_carrier);
        if (mode === 'award' && d.cheapest_award_taxes != null && d.cheapest_award_taxes > 0) {
          metaParts.push('+' + FH.core.fmtMoney(d.cheapest_award_taxes));
        }
        if (mode === 'both' && d.cheapest_award_miles) {
          metaParts.push(Number(d.cheapest_award_miles).toLocaleString() + ' mi');
        }
        const meta = metaParts.length
          ? '<div class="fh-cal-cell__meta">' + FH.core.escape(metaParts.join(' · ')) + '</div>'
          : '<div class="fh-cal-cell__meta">&nbsp;</div>';
        const interactive = (val != null);
        html += '<div class="fh-cal-cell ' + cls + '"' +
                ' data-i="' + idx + '" data-date="' + FH.core.escape(d.date) + '"' +
                (interactive ? '' : ' aria-disabled="true"') + '>' +
                '<div class="fh-cal-cell__date">' + dateNum + '</div>' +
                priceHtml + meta +
                '</div>';
      });
      cal.innerHTML = html;

      cal.querySelectorAll('.fh-cal-cell[data-date]').forEach(function (cell) {
        if (cell.classList.contains('fh-cal-cell--nodata') ||
            cell.classList.contains('fh-cal-cell--filler')) return;
        cell.addEventListener('click', function () {
          FH.flex.drilldown(cell.dataset.date, q);
        });
      });

      legend.innerHTML = ''
        + '<span><span class="fh-cal-legend__swatch fh-cal-legend__swatch--cheap"></span>Cheap (≤ Q1)</span>'
        + '<span><span class="fh-cal-legend__swatch fh-cal-legend__swatch--mid-low"></span>Below median</span>'
        + '<span><span class="fh-cal-legend__swatch fh-cal-legend__swatch--mid-high"></span>Above median</span>'
        + '<span><span class="fh-cal-legend__swatch fh-cal-legend__swatch--expensive"></span>Top 10%</span>'
        + '<span><span class="fh-cal-legend__swatch fh-cal-legend__swatch--nodata"></span>No data</span>';

      wrap.hidden = false;
    },

    // Click a calendar cell → pre-fill the SEARCH form for that date and navigate.
    drilldown: function (date, q) {
      if (!date || !q) return;
      FH.search.state.origin = [q.origin];
      FH.search.state.dest   = [q.destination];
      if (typeof FH.search.paintChips === 'function') {
        FH.search.paintChips('origin');
        FH.search.paintChips('dest');
      }
      const depart = document.getElementById('fh-depart-from');
      const ret    = document.getElementById('fh-return-from');
      const oneway = document.getElementById('fh-oneway');
      if (depart) depart.value = date;
      if (ret)    ret.value    = '';
      if (oneway) {
        oneway.checked = true;
        if (typeof FH.search.applyOneWay === 'function') FH.search.applyOneWay();
      }
      const wantCabin = q.cabin || 'Y';
      document.querySelectorAll('input[name=cabin]').forEach(function (cb) {
        cb.checked = (cb.value === wantCabin);
      });
      const modeRadio = document.querySelector('input[name=fh-mode][value=' + (q.mode || 'cash') + ']');
      if (modeRadio) modeRadio.checked = true;
      const adults = document.getElementById('fh-adults');
      if (adults) adults.value = q.adults || 1;

      FH.core.go('search');
      FH.core.footer(
        'Pre-filled search for ' + q.origin + ' → ' + q.destination + ' on ' + date,
        null
      );
    }
  };

  // ============================================================ //
  // BOOT                                                         //
  // ============================================================ //

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', FH.core.init);
  } else {
    FH.core.init();
  }
})();
