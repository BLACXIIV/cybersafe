/* Live password strength feedback shared by the signup form and the admin
   "change password" modal. Mirrors security.py so students (and admins) get
   instant feedback; the server re-checks everything on submit, so this is a
   convenience layer, not the gate. */
(function() {
  const MIN_LENGTH = 10;
  const MIN_DISTINCT = 6;
  const COMMON = [
    'password', 'passcode', 'letmein', 'welcome', 'changeme', 'default', 'secret',
    'qwerty', 'qwertyuiop', 'asdfgh', 'asdfghjkl', 'zxcvbn', 'azerty', 'abcabc',
    'abcdef', 'iloveyou', 'monkey', 'dragon', 'sunshine', 'princess', 'football',
    'basketball', 'baseball', 'soccer', 'hockey', 'starwars', 'pokemon', 'superman',
    'batman', 'shadow', 'master', 'login', 'admin', 'administrator', 'guest',
    'testing', 'trustno', 'whatever', 'freedom', 'hello', 'hellothere', 'computer',
    'internet', 'google', 'facebook', 'youtube', 'tiktok', 'cybersafe', 'cyber',
    'school', 'student', 'teacher', 'grade', 'philippines', 'pilipinas', 'manila',
    'mahalkita'
  ];
  const WALK_ROWS = ['qwertyuiop', 'asdfghjkl', 'zxcvbnm', '1qaz2wsx',
                     'abcdefghijklmnopqrstuvwxyz', '0123456789'];
  const LEET = { '@': 'a', '4': 'a', '8': 'b', '(': 'c', '3': 'e', '6': 'g', '9': 'g',
                 '1': 'i', '!': 'i', '|': 'i', '0': 'o', '5': 's', '$': 's', '7': 't',
                 '+': 't', '2': 'z' };

  const WALKS = new Set();
  WALK_ROWS.forEach(function(row) {
    [row, row.split('').reverse().join('')].forEach(function(dir) {
      for (let i = 0; i <= dir.length - 4; i++) WALKS.add(dir.slice(i, i + 4));
    });
  });

  function normalise(value) {
    return value.toLowerCase().replace(/./g, function(ch) { return LEET[ch] || ch; })
                .replace(/[^a-z0-9]/g, '');
  }

  function personalTerms(values) {
    const terms = new Set();
    values.forEach(function(value) {
      String(value || '').split('@')[0].split(/[^A-Za-z0-9]+/).forEach(function(word) {
        if (word.length >= 3) terms.add(word.toLowerCase());
      });
    });
    return terms;
  }

  function evaluate(pw, confirmValue, personalValues) {
    const norm = normalise(pw);
    const lower = pw.toLowerCase();
    const base = norm.replace(/\d+$/, '');

    let personalHit = false;
    personalTerms(personalValues).forEach(function(term) {
      const t = normalise(term);
      if (t && norm.includes(t)) personalHit = true;
    });

    const commonHit = COMMON.includes(norm) || COMMON.includes(base) ||
      COMMON.some(function(word) { return word.length >= 5 && norm.includes(word); });
    const walkHit = Array.from(WALKS).some(function(run) { return lower.includes(run); });
    const repeatHit = /(.)\1{2,}/.test(pw);

    let periodic = false;
    for (let unit = 1; unit <= pw.length / 2; unit++) {
      if (pw.length % unit === 0 && pw.slice(0, unit).repeat(pw.length / unit) === pw) {
        periodic = true;
        break;
      }
    }
    const varietyHit = periodic || new Set(pw).size < MIN_DISTINCT;

    return {
      length: pw.length >= MIN_LENGTH,
      lower: /[a-z]/.test(pw),
      upper: /[A-Z]/.test(pw),
      number: /[0-9]/.test(pw),
      symbol: /[^A-Za-z0-9]/.test(pw),
      uncommon: pw.length > 0 && !commonHit && !walkHit && !repeatHit && !varietyHit,
      personal: pw.length > 0 && !personalHit,
      match: pw.length > 0 && pw === confirmValue
    };
  }

  /* options: { password, confirm, rules, meterFill, meterText,
                personal: () => [values], watch: [elements] } */
  window.initPasswordStrength = function(options) {
    const password = options.password;
    const confirm = options.confirm;
    const rules = options.rules;
    if (!password || !confirm || !rules) return null;

    const ruleItems = Array.from(rules.children);
    const meterFill = options.meterFill;
    const meterText = options.meterText;
    const personal = options.personal || function() { return []; };

    function render() {
      const results = evaluate(password.value, confirm.value, personal());
      const empty = password.value.length === 0;
      let passed = 0;

      ruleItems.forEach(function(item) {
        const ok = results[item.dataset.rule];
        item.classList.toggle('ok', !empty && ok);
        item.classList.toggle('bad', !empty && !ok);
        if (ok) passed++;
      });

      const ratio = empty ? 0 : passed / ruleItems.length;
      if (meterFill) meterFill.style.width = Math.round(ratio * 100) + '%';

      let label = '—';
      let state = '';
      if (!empty) {
        if (ratio < 0.5) { label = 'Weak'; state = 'weak'; }
        else if (ratio < 1) { label = 'Almost there'; state = 'medium'; }
        else { label = 'Strong'; state = 'strong'; }
      }
      if (meterText) meterText.textContent = label;
      if (meterFill) meterFill.className = state;
    }

    ['input', 'blur'].forEach(function(evt) {
      password.addEventListener(evt, render);
      confirm.addEventListener(evt, render);
    });
    (options.watch || []).forEach(function(el) {
      if (el) el.addEventListener('input', render);
    });

    render();
    return { render: render };
  };
})();
