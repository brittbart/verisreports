/* Compiled from tweaks-panel.jsx by tools/build_methodology.js (@babel/standalone 7.29.0). Do not edit. */
"use strict";

function _typeof(o) { "@babel/helpers - typeof"; return _typeof = "function" == typeof Symbol && "symbol" == typeof Symbol.iterator ? function (o) { return typeof o; } : function (o) { return o && "function" == typeof Symbol && o.constructor === Symbol && o !== Symbol.prototype ? "symbol" : typeof o; }, _typeof(o); }
function ownKeys(e, r) { var t = Object.keys(e); if (Object.getOwnPropertySymbols) { var o = Object.getOwnPropertySymbols(e); r && (o = o.filter(function (r) { return Object.getOwnPropertyDescriptor(e, r).enumerable; })), t.push.apply(t, o); } return t; }
function _objectSpread(e) { for (var r = 1; r < arguments.length; r++) { var t = null != arguments[r] ? arguments[r] : {}; r % 2 ? ownKeys(Object(t), !0).forEach(function (r) { _defineProperty(e, r, t[r]); }) : Object.getOwnPropertyDescriptors ? Object.defineProperties(e, Object.getOwnPropertyDescriptors(t)) : ownKeys(Object(t)).forEach(function (r) { Object.defineProperty(e, r, Object.getOwnPropertyDescriptor(t, r)); }); } return e; }
function _defineProperty(e, r, t) { return (r = _toPropertyKey(r)) in e ? Object.defineProperty(e, r, { value: t, enumerable: !0, configurable: !0, writable: !0 }) : e[r] = t, e; }
function _toPropertyKey(t) { var i = _toPrimitive(t, "string"); return "symbol" == _typeof(i) ? i : i + ""; }
function _toPrimitive(t, r) { if ("object" != _typeof(t) || !t) return t; var e = t[Symbol.toPrimitive]; if (void 0 !== e) { var i = e.call(t, r || "default"); if ("object" != _typeof(i)) return i; throw new TypeError("@@toPrimitive must return a primitive value."); } return ("string" === r ? String : Number)(t); }
function _slicedToArray(r, e) { return _arrayWithHoles(r) || _iterableToArrayLimit(r, e) || _unsupportedIterableToArray(r, e) || _nonIterableRest(); }
function _nonIterableRest() { throw new TypeError("Invalid attempt to destructure non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method."); }
function _unsupportedIterableToArray(r, a) { if (r) { if ("string" == typeof r) return _arrayLikeToArray(r, a); var t = {}.toString.call(r).slice(8, -1); return "Object" === t && r.constructor && (t = r.constructor.name), "Map" === t || "Set" === t ? Array.from(r) : "Arguments" === t || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t) ? _arrayLikeToArray(r, a) : void 0; } }
function _arrayLikeToArray(r, a) { (null == a || a > r.length) && (a = r.length); for (var e = 0, n = Array(a); e < a; e++) n[e] = r[e]; return n; }
function _iterableToArrayLimit(r, l) { var t = null == r ? null : "undefined" != typeof Symbol && r[Symbol.iterator] || r["@@iterator"]; if (null != t) { var e, n, i, u, a = [], f = !0, o = !1; try { if (i = (t = t.call(r)).next, 0 === l) { if (Object(t) !== t) return; f = !1; } else for (; !(f = (e = i.call(t)).done) && (a.push(e.value), a.length !== l); f = !0); } catch (r) { o = !0, n = r; } finally { try { if (!f && null != t["return"] && (u = t["return"](), Object(u) !== u)) return; } finally { if (o) throw n; } } return a; } }
function _arrayWithHoles(r) { if (Array.isArray(r)) return r; }
// tweaks-panel.jsx
// Reusable Tweaks shell + form-control helpers.
//
// Owns the host protocol (listens for __activate_edit_mode / __deactivate_edit_mode,
// posts __edit_mode_available / __edit_mode_set_keys / __edit_mode_dismissed) so
// individual prototypes don't re-roll it. Ships a consistent set of controls so you
// don't hand-draw <input type="range">, segmented radios, steppers, etc.
//
// Usage (in an HTML file that loads React + Babel):
function useTweaks(defaults) {
  var _React$useState = React.useState(defaults),
    _React$useState2 = _slicedToArray(_React$useState, 2),
    values = _React$useState2[0],
    setValues = _React$useState2[1];
  var setTweak = React.useCallback(function (key, val) {
    setValues(function (prev) {
      return _objectSpread(_objectSpread({}, prev), {}, _defineProperty({}, key, val));
    });
    window.parent.postMessage({
      type: '__edit_mode_set_keys',
      edits: _defineProperty({}, key, val)
    }, '*');
  }, []);
  return [values, setTweak];
}
function TweaksPanel(_ref) {
  var _ref$title = _ref.title,
    title = _ref$title === void 0 ? 'Tweaks' : _ref$title,
    children = _ref.children;
  var _React$useState3 = React.useState(false),
    _React$useState4 = _slicedToArray(_React$useState3, 2),
    open = _React$useState4[0],
    setOpen = _React$useState4[1];
  React.useEffect(function () {
    var onMsg = function onMsg(e) {
      var _e$data;
      var t = e === null || e === void 0 || (_e$data = e.data) === null || _e$data === void 0 ? void 0 : _e$data.type;
      if (t === '__activate_edit_mode') setOpen(true);else if (t === '__deactivate_edit_mode') setOpen(false);
    };
    window.addEventListener('message', onMsg);
    window.parent.postMessage({
      type: '__edit_mode_available'
    }, '*');
    return function () {
      return window.removeEventListener('message', onMsg);
    };
  }, []);
  if (!open) return null;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'fixed',
      right: '16px',
      bottom: '16px',
      zIndex: 9999,
      background: 'rgba(250,249,247,.95)',
      borderRadius: '14px',
      padding: '16px',
      width: '280px',
      boxShadow: '0 12px 40px rgba(0,0,0,.18)'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      marginBottom: '12px'
    }
  }, /*#__PURE__*/React.createElement("b", {
    style: {
      fontSize: '12px'
    }
  }, title), /*#__PURE__*/React.createElement("button", {
    onClick: function onClick() {
      return setOpen(false);
    },
    style: {
      background: 'none',
      border: 'none',
      cursor: 'default',
      fontSize: '16px'
    }
  }, "\u2715")), /*#__PURE__*/React.createElement("div", null, children));
}
function TweakSection(_ref2) {
  var title = _ref2.title,
    children = _ref2.children;
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '10px',
      fontWeight: 600,
      textTransform: 'uppercase',
      color: 'rgba(40,30,20,.45)',
      padding: '10px 0 6px'
    }
  }, title), children);
}
function TweakRadio(_ref3) {
  var label = _ref3.label,
    value = _ref3.value,
    options = _ref3.options,
    onChange = _ref3.onChange;
  var opts = options.map(function (o) {
    return _typeof(o) === 'object' ? o : {
      value: o,
      label: o
    };
  });
  return /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: '8px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '11px',
      color: 'rgba(40,30,20,.7)',
      marginBottom: '4px'
    }
  }, label), /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      gap: '4px'
    }
  }, opts.map(function (o) {
    return /*#__PURE__*/React.createElement("button", {
      key: o.value,
      type: "button",
      onClick: function onClick() {
        return onChange(o.value);
      },
      style: {
        flex: 1,
        padding: '4px',
        borderRadius: '6px',
        border: '.5px solid rgba(0,0,0,.15)',
        background: o.value === value ? 'rgba(255,255,255,.9)' : 'transparent',
        weight: o.value === value ? 600 : 400,
        textAlign: 'center',
        fontSize: '11px',
        color: o.value === value ? '#29261b' : 'rgba(41,38,27,.55)',
        cursor: 'default'
      }
    }, o.label);
  })));
}
function TweakSelect(_ref4) {
  var label = _ref4.label,
    value = _ref4.value,
    options = _ref4.options,
    _onChange = _ref4.onChange;
  return /*#__PURE__*/React.createElement("div", {
    style: {
      marginBottom: '8px'
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: '11px',
      color: 'rgba(40,30,20,.7)',
      marginBottom: '4px'
    }
  }, label), /*#__PURE__*/React.createElement("select", {
    value: value,
    onChange: function onChange(e) {
      return _onChange(e.target.value);
    },
    style: {
      width: '100%',
      padding: '4px',
      borderRadius: '6px',
      border: '.5px solid rgba(0,0,0,.15)',
      background: 'rgba(255,255,255,.6)',
      fontSize: '11px'
    }
  }, options.map(function (o) {
    var v = _typeof(o) === 'object' ? o.value : o;
    var l = _typeof(o) === 'object' ? o.label : o;
    return /*#__PURE__*/React.createElement("option", {
      key: v,
      value: v
    }, l);
  })));
}
Object.assign(window, {
  useTweaks: useTweaks,
  TweaksPanel: TweaksPanel,
  TweakSection: TweakSection,
  TweakRadio: TweakRadio,
  TweakSelect: TweakSelect
});
