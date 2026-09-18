/* Compiled from methodology-boot.jsx by tools/build_methodology.js (@babel/standalone 7.29.0). Do not edit. */
"use strict";

function _slicedToArray(r, e) { return _arrayWithHoles(r) || _iterableToArrayLimit(r, e) || _unsupportedIterableToArray(r, e) || _nonIterableRest(); }
function _nonIterableRest() { throw new TypeError("Invalid attempt to destructure non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method."); }
function _unsupportedIterableToArray(r, a) { if (r) { if ("string" == typeof r) return _arrayLikeToArray(r, a); var t = {}.toString.call(r).slice(8, -1); return "Object" === t && r.constructor && (t = r.constructor.name), "Map" === t || "Set" === t ? Array.from(r) : "Arguments" === t || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t) ? _arrayLikeToArray(r, a) : void 0; } }
function _arrayLikeToArray(r, a) { (null == a || a > r.length) && (a = r.length); for (var e = 0, n = Array(a); e < a; e++) n[e] = r[e]; return n; }
function _iterableToArrayLimit(r, l) { var t = null == r ? null : "undefined" != typeof Symbol && r[Symbol.iterator] || r["@@iterator"]; if (null != t) { var e, n, i, u, a = [], f = !0, o = !1; try { if (i = (t = t.call(r)).next, 0 === l) { if (Object(t) !== t) return; f = !1; } else for (; !(f = (e = i.call(t)).done) && (a.push(e.value), a.length !== l); f = !0); } catch (r) { o = !0, n = r; } finally { try { if (!f && null != t["return"] && (u = t["return"](), Object(u) !== u)) return; } finally { if (o) throw n; } } return a; } }
function _arrayWithHoles(r) { if (Array.isArray(r)) return r; }
var _React = React,
  useState = _React.useState,
  useEffect = _React.useEffect;
var DEFAULTS = {
  "mode": "dark",
  "display": "default",
  "accent": "default",
  "density": "comfortable"
};
var ACCENTS = {
  "default": "#e879f9",
  violet: "#a855f7",
  cyan: "#22d3ee",
  amber: "#fbbf24",
  emerald: "#34d399"
};
function applyTweaks(tweaks) {
  document.querySelectorAll('.vs-theme').forEach(function (el) {
    var _ACCENTS$tweaks$accen;
    if (tweaks.mode === 'light') el.setAttribute('data-vs-light', '');else el.removeAttribute('data-vs-light');
    el.setAttribute('data-vs-density', tweaks.density);
    if (tweaks.display !== 'default') el.setAttribute('data-vs-display', tweaks.display);else el.removeAttribute('data-vs-display');
    var accentVal = (_ACCENTS$tweaks$accen = ACCENTS[tweaks.accent]) !== null && _ACCENTS$tweaks$accen !== void 0 ? _ACCENTS$tweaks$accen : ACCENTS["default"];
    if (tweaks.accent !== 'default') el.style.setProperty('--accent', accentVal);else el.style.removeProperty('--accent');
  });
}
function TweaksHost() {
  var _useTweaks = useTweaks(DEFAULTS),
    _useTweaks2 = _slicedToArray(_useTweaks, 2),
    tweaks = _useTweaks2[0],
    setTweaks = _useTweaks2[1];
  useEffect(function () {
    applyTweaks(tweaks);
  }, [tweaks]);
  useEffect(function () {
    var id = setTimeout(function () {
      return applyTweaks(tweaks);
    }, 50);
    return function () {
      return clearTimeout(id);
    };
  }, []);
  return /*#__PURE__*/React.createElement(TweaksPanel, {
    title: "Tweaks"
  }, /*#__PURE__*/React.createElement(TweakSection, {
    title: "Appearance"
  }, /*#__PURE__*/React.createElement(TweakRadio, {
    label: "Mode",
    value: tweaks.mode,
    onChange: function onChange(v) {
      return setTweaks({
        mode: v
      });
    },
    options: [{
      value: 'dark',
      label: 'Dark'
    }, {
      value: 'light',
      label: 'Light'
    }]
  }), /*#__PURE__*/React.createElement(TweakRadio, {
    label: "Display font",
    value: tweaks.display,
    onChange: function onChange(v) {
      return setTweaks({
        display: v
      });
    },
    options: [{
      value: 'default',
      label: 'Default'
    }, {
      value: 'sans',
      label: 'Sans'
    }, {
      value: 'serif',
      label: 'Serif'
    }]
  }), /*#__PURE__*/React.createElement(TweakRadio, {
    label: "Density",
    value: tweaks.density,
    onChange: function onChange(v) {
      return setTweaks({
        density: v
      });
    },
    options: [{
      value: 'comfortable',
      label: 'Comfortable'
    }, {
      value: 'compact',
      label: 'Compact'
    }]
  })), /*#__PURE__*/React.createElement(TweakSection, {
    title: "Accent"
  }, /*#__PURE__*/React.createElement(TweakSelect, {
    label: "Accent",
    value: tweaks.accent,
    onChange: function onChange(v) {
      return setTweaks({
        accent: v
      });
    },
    options: [{
      value: 'default',
      label: 'Fuchsia (default)'
    }, {
      value: 'violet',
      label: 'Violet'
    }, {
      value: 'cyan',
      label: 'Cyan'
    }, {
      value: 'amber',
      label: 'Amber'
    }, {
      value: 'emerald',
      label: 'Emerald'
    }]
  })));
}
function App() {
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "vs-theme vs-theme--a",
    "data-theme-key": "a"
  }, /*#__PURE__*/React.createElement(Report, null)), /*#__PURE__*/React.createElement(TweaksHost, null));
}
ReactDOM.createRoot(document.getElementById('root')).render(/*#__PURE__*/React.createElement(App, null));
