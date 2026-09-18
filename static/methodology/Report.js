/* Compiled from Report.jsx by tools/build_methodology.js (@babel/standalone 7.29.0). Do not edit. */
"use strict";

function _createForOfIteratorHelper(r, e) { var t = "undefined" != typeof Symbol && r[Symbol.iterator] || r["@@iterator"]; if (!t) { if (Array.isArray(r) || (t = _unsupportedIterableToArray(r)) || e && r && "number" == typeof r.length) { t && (r = t); var _n = 0, F = function F() {}; return { s: F, n: function n() { return _n >= r.length ? { done: !0 } : { done: !1, value: r[_n++] }; }, e: function e(r) { throw r; }, f: F }; } throw new TypeError("Invalid attempt to iterate non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method."); } var o, a = !0, u = !1; return { s: function s() { t = t.call(r); }, n: function n() { var r = t.next(); return a = r.done, r; }, e: function e(r) { u = !0, o = r; }, f: function f() { try { a || null == t["return"] || t["return"](); } finally { if (u) throw o; } } }; }
function _slicedToArray(r, e) { return _arrayWithHoles(r) || _iterableToArrayLimit(r, e) || _unsupportedIterableToArray(r, e) || _nonIterableRest(); }
function _nonIterableRest() { throw new TypeError("Invalid attempt to destructure non-iterable instance.\nIn order to be iterable, non-array objects must have a [Symbol.iterator]() method."); }
function _unsupportedIterableToArray(r, a) { if (r) { if ("string" == typeof r) return _arrayLikeToArray(r, a); var t = {}.toString.call(r).slice(8, -1); return "Object" === t && r.constructor && (t = r.constructor.name), "Map" === t || "Set" === t ? Array.from(r) : "Arguments" === t || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(t) ? _arrayLikeToArray(r, a) : void 0; } }
function _arrayLikeToArray(r, a) { (null == a || a > r.length) && (a = r.length); for (var e = 0, n = Array(a); e < a; e++) n[e] = r[e]; return n; }
function _iterableToArrayLimit(r, l) { var t = null == r ? null : "undefined" != typeof Symbol && r[Symbol.iterator] || r["@@iterator"]; if (null != t) { var e, n, i, u, a = [], f = !0, o = !1; try { if (i = (t = t.call(r)).next, 0 === l) { if (Object(t) !== t) return; f = !1; } else for (; !(f = (e = i.call(t)).done) && (a.push(e.value), a.length !== l); f = !0); } catch (r) { o = !0, n = r; } finally { try { if (!f && null != t["return"] && (u = t["return"](), Object(u) !== u)) return; } finally { if (o) throw n; } } return a; } }
function _arrayWithHoles(r) { if (Array.isArray(r)) return r; }
var _React = React,
  useState = _React.useState,
  useEffect = _React.useEffect,
  useRef = _React.useRef;
function cx() {
  for (var _len = arguments.length, parts = new Array(_len), _key = 0; _key < _len; _key++) {
    parts[_key] = arguments[_key];
  }
  return parts.filter(Boolean).join(' ');
}
var VERDICT_META = {
  supported: {
    label: "SUPPORTED",
    weight: "+1.0",
    tone: "pos",
    blurb: "Confirmed by two independent sources."
  },
  plausible: {
    label: "PLAUSIBLE",
    weight: "+0.5",
    tone: "pos",
    blurb: "Consistent with evidence; only one credible source found."
  },
  corroborated: {
    label: "CORROBORATED",
    weight: "+0.75",
    tone: "pos",
    blurb: "5+ outlets report consistently without contradiction."
  },
  overstated: {
    label: "OVERSTATED",
    weight: "-0.5",
    tone: "neg",
    blurb: "Core fact is real but exaggerated or framed misleadingly."
  },
  disputed: {
    label: "DISPUTED",
    weight: "-1.0",
    tone: "neg",
    blurb: "At least one credible source directly contradicts the claim."
  },
  not_supported: {
    label: "NOT_SUPPORTED",
    weight: "-1.5",
    tone: "neg",
    blurb: "Evidence actively contradicts the claim."
  },
  not_verifiable: {
    label: "NOT_VERIFIABLE",
    weight: "excl.",
    tone: "neutral",
    blurb: "Cannot confirm or deny -- sources unavailable."
  },
  opinion: {
    label: "OPINION",
    weight: "excl.",
    tone: "neutral",
    blurb: "Editorial or opinion content -- not a factual signal."
  }
};
function TOC(_ref) {
  var sections = _ref.sections,
    activeId = _ref.activeId,
    onJump = _ref.onJump;
  return /*#__PURE__*/React.createElement("nav", {
    className: "vs-toc",
    "aria-label": "Table of contents"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-toc__label"
  }, "CONTENTS"), /*#__PURE__*/React.createElement("ol", null, sections.map(function (s) {
    return /*#__PURE__*/React.createElement("li", {
      key: s.id
    }, /*#__PURE__*/React.createElement("a", {
      href: "#" + s.id,
      className: cx("vs-toc__link", activeId === s.id && "is-active"),
      onClick: function onClick(e) {
        e.preventDefault();
        onJump(s.id);
      }
    }, /*#__PURE__*/React.createElement("span", {
      className: "vs-toc__num"
    }, s.num), /*#__PURE__*/React.createElement("span", {
      className: "vs-toc__title"
    }, s.title.replace(/^Stage \d+ \u2014 /, ""))));
  })), /*#__PURE__*/React.createElement("div", {
    className: "vs-toc__footer"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-toc__brand"
  }, /*#__PURE__*/React.createElement("svg", {
    width: "220",
    height: "32",
    viewBox: "0 0 185 28",
    xmlns: "http://www.w3.org/2000/svg"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M4 14 Q7 6 10 14 Q13 22 16 14 Q19 6 22 14",
    fill: "none",
    stroke: "#a855f7",
    strokeWidth: "2",
    strokeLinecap: "round",
    strokeLinejoin: "round"
  }), /*#__PURE__*/React.createElement("circle", {
    cx: "25",
    cy: "14",
    r: "2.5",
    fill: "#ec4899"
  }), /*#__PURE__*/React.createElement("text", {
    x: "32",
    y: "19",
    fontFamily: "Trebuchet MS,sans-serif",
    fontSize: "13",
    fontWeight: "700",
    fill: "#ffffff",
    letterSpacing: "1.5"
  }, "VERUM"), /*#__PURE__*/React.createElement("text", {
    x: "88",
    y: "19",
    fontFamily: "Trebuchet MS,sans-serif",
    fontSize: "13",
    fontWeight: "400",
    fontStyle: "italic",
    fill: "#c084fc",
    letterSpacing: "1.5",
    transform: "skewX(-6)"
  }, "SIGNAL"))), /*#__PURE__*/React.createElement("div", {
    className: "vs-toc__meta"
  }, "Methodology ", "\xB7", " ", window.VS_DATA.meta.version), /*#__PURE__*/React.createElement("div", {
    className: "vs-toc__meta"
  }, window.VS_DATA.meta.date)));
}
function Masthead(_ref2) {
  var meta = _ref2.meta;
  return /*#__PURE__*/React.createElement("header", {
    className: "vs-masthead"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-masthead__row"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-masthead__brand"
  }, /*#__PURE__*/React.createElement("span", {
    className: "vs-logo__verum"
  }, "VERUM "), /*#__PURE__*/React.createElement("span", {
    className: "vs-logo__signal"
  }, "SIGNAL")), /*#__PURE__*/React.createElement("div", {
    className: "vs-masthead__meta"
  }, /*#__PURE__*/React.createElement("span", null, "METHODOLOGY"), /*#__PURE__*/React.createElement("span", {
    className: "vs-dot"
  }), /*#__PURE__*/React.createElement("span", null, meta.version), /*#__PURE__*/React.createElement("span", {
    className: "vs-dot"
  }), /*#__PURE__*/React.createElement("span", null, meta.date))), /*#__PURE__*/React.createElement("div", {
    className: "vs-masthead__hero"
  }, /*#__PURE__*/React.createElement("h1", {
    className: "vs-display"
  }, meta.title), /*#__PURE__*/React.createElement("p", {
    className: "vs-lede"
  }, meta.subtitle), /*#__PURE__*/React.createElement("p", {
    className: "vs-principle"
  }, meta.principle)));
}
function PipelineDiagram() {
  var stages = [{
    n: "01",
    title: "Ingestion",
    note: "URL \u2192 article text"
  }, {
    n: "02",
    title: "Extraction",
    note: "Sonnet \u2192 claim list"
  }, {
    n: "03",
    title: "Priority",
    note: "score \u2265 30 \u2192 queue"
  }, {
    n: "04",
    title: "Verification",
    note: "opinion pre-filter \xB7 web search"
  }];
  var _useState = useState(0),
    _useState2 = _slicedToArray(_useState, 2),
    active = _useState2[0],
    setActive = _useState2[1];
  useEffect(function () {
    var id = setInterval(function () {
      return setActive(function (a) {
        return (a + 1) % stages.length;
      });
    }, 1800);
    return function () {
      return clearInterval(id);
    };
  }, []);
  return /*#__PURE__*/React.createElement("div", {
    className: "vs-pipeline"
  }, stages.map(function (s, i) {
    return /*#__PURE__*/React.createElement(React.Fragment, {
      key: s.n
    }, /*#__PURE__*/React.createElement("div", {
      className: cx("vs-pipeline__node", active === i && "is-active"),
      onMouseEnter: function onMouseEnter() {
        return setActive(i);
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "vs-pipeline__num"
    }, s.n), /*#__PURE__*/React.createElement("div", {
      className: "vs-pipeline__title"
    }, s.title), /*#__PURE__*/React.createElement("div", {
      className: "vs-pipeline__note"
    }, s.note), /*#__PURE__*/React.createElement("div", {
      className: "vs-pipeline__pulse"
    })), i < stages.length - 1 && /*#__PURE__*/React.createElement("div", {
      className: cx("vs-pipeline__edge", active > i && "is-past", active === i + 1 && "is-live")
    }, /*#__PURE__*/React.createElement("span", {
      className: "vs-pipeline__spark"
    })));
  }));
}
function SectionRenderer(_ref3) {
  var section = _ref3.section;
  var s = section;
  return /*#__PURE__*/React.createElement("section", {
    id: s.id,
    className: "vs-section"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-section__head"
  }, /*#__PURE__*/React.createElement("span", {
    className: "vs-section__num"
  }, s.num), /*#__PURE__*/React.createElement("h2", {
    className: "vs-section__title"
  }, s.title)), /*#__PURE__*/React.createElement("div", {
    className: "vs-section__body"
  }, (s.body || []).map(function (p, i) {
    return /*#__PURE__*/React.createElement("p", {
      key: i,
      className: "vs-p"
    }, p);
  }), (s.attribution || []).length > 0 && /*#__PURE__*/React.createElement("ul", {
    className: "vs-checks"
  }, s.attribution.map(function (_ref4, i) {
    var _ref5 = _slicedToArray(_ref4, 2),
      title = _ref5[0],
      desc = _ref5[1];
    return /*#__PURE__*/React.createElement("li", {
      key: i
    }, /*#__PURE__*/React.createElement("b", null, title), desc && " -- " + desc);
  })), s.kind === "pipeline" && /*#__PURE__*/React.createElement(PipelineDiagram, null), (s.steps || []).map(function (st, i) {
    return /*#__PURE__*/React.createElement("div", {
      key: "step" + i,
      className: "vs-sub"
    }, /*#__PURE__*/React.createElement("div", {
      className: "vs-sub__title"
    }, "Step " + st.num + " \u2014 " + st.title), /*#__PURE__*/React.createElement("p", {
      className: "vs-p"
    }, st.body));
  }), (s.sub || []).map(function (sub, i) {
    return /*#__PURE__*/React.createElement("div", {
      key: i,
      className: "vs-sub"
    }, /*#__PURE__*/React.createElement("div", {
      className: "vs-sub__title"
    }, sub.title), sub.intro && /*#__PURE__*/React.createElement("p", {
      className: "vs-p"
    }, sub.intro), (sub.items || []).length > 0 && /*#__PURE__*/React.createElement("ul", {
      className: "vs-checks"
    }, sub.items.map(function (_ref6, j) {
      var _ref7 = _slicedToArray(_ref6, 2),
        title = _ref7[0],
        desc = _ref7[1];
      return /*#__PURE__*/React.createElement("li", {
        key: j
      }, /*#__PURE__*/React.createElement("b", null, title), desc && " -- " + desc);
    })));
  }), (s.checks || []).length > 0 && /*#__PURE__*/React.createElement("ul", {
    className: "vs-checks"
  }, s.checks.map(function (_ref8, i) {
    var _ref9 = _slicedToArray(_ref8, 2),
      title = _ref9[0],
      desc = _ref9[1];
    return /*#__PURE__*/React.createElement("li", {
      key: i
    }, /*#__PURE__*/React.createElement("b", null, title), desc && " -- " + desc);
  })), s.kind === "verdicts" && s.verdicts && /*#__PURE__*/React.createElement("div", {
    className: "vs-legend"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-legend__header"
  }, /*#__PURE__*/React.createElement("span", null, "VERDICT"), /*#__PURE__*/React.createElement("span", null, "WEIGHT"), /*#__PURE__*/React.createElement("span", null, "MEANING")), s.verdicts.map(function (v) {
    return /*#__PURE__*/React.createElement("div", {
      key: v.key,
      className: cx("vs-legend__row", "is-" + v.tone)
    }, /*#__PURE__*/React.createElement("span", {
      className: cx("vs-verdict", "is-" + v.tone)
    }, v.key.toUpperCase().replace("_", " ")), /*#__PURE__*/React.createElement("span", {
      className: "vs-legend__weight"
    }, v.weight), /*#__PURE__*/React.createElement("span", {
      className: "vs-legend__blurb"
    }, v.meaning));
  })), s.kind === "score" && s.formula && /*#__PURE__*/React.createElement("div", null, s.formula.steps.map(function (step, i) {
    return /*#__PURE__*/React.createElement("div", {
      key: i,
      className: step.highlight ? "vs-formula-line vs-formula-line--highlight" : "vs-formula-line"
    }, /*#__PURE__*/React.createElement("span", {
      className: "vs-formula-label"
    }, step.label), /*#__PURE__*/React.createElement("span", null, step.expr));
  }), s.formula.note && /*#__PURE__*/React.createElement("p", {
    className: "vs-formula-note"
  }, s.formula.note)), s.callout && /*#__PURE__*/React.createElement("aside", {
    className: "vs-callout"
  }, /*#__PURE__*/React.createElement("div", {
    className: "vs-callout__label"
  }, s.callout.label), /*#__PURE__*/React.createElement("div", {
    className: "vs-callout__text"
  }, s.callout.text)), s.bodyAfter && /*#__PURE__*/React.createElement("p", {
    className: "vs-p"
  }, s.bodyAfter)));
}
function Report() {
  var data = window.VS_DATA;
  if (!data) return /*#__PURE__*/React.createElement("div", null, "Loading...");
  var _useState3 = useState(data.sections[0].id),
    _useState4 = _slicedToArray(_useState3, 2),
    activeId = _useState4[0],
    setActiveId = _useState4[1];
  var mainRef = useRef(null);
  useEffect(function () {
    var main = mainRef.current;
    if (!main) return;
    var handleScroll = function handleScroll() {
      var ids = data.sections.map(function (s) {
        return s.id;
      });
      var _iterator = _createForOfIteratorHelper(ids),
        _step;
      try {
        for (_iterator.s(); !(_step = _iterator.n()).done;) {
          var id = _step.value;
          var el = document.getElementById(id);
          if (el && el.getBoundingClientRect().top < main.clientHeight / 2) {
            setActiveId(id);
          }
        }
      } catch (err) {
        _iterator.e(err);
      } finally {
        _iterator.f();
      }
    };
    main.addEventListener("scroll", handleScroll);
    return function () {
      return main.removeEventListener("scroll", handleScroll);
    };
  }, [data]);
  var onJump = function onJump(id) {
    var el = document.getElementById(id);
    if (el) el.scrollIntoView({
      behavior: "smooth"
    });
    setActiveId(id);
  };
  return /*#__PURE__*/React.createElement("div", {
    className: "vs-root"
  }, /*#__PURE__*/React.createElement("aside", {
    className: "vs-sidebar"
  }, /*#__PURE__*/React.createElement(TOC, {
    sections: data.sections,
    activeId: activeId,
    onJump: onJump
  })), /*#__PURE__*/React.createElement("main", {
    className: "vs-main",
    ref: mainRef
  }, /*#__PURE__*/React.createElement(Masthead, {
    meta: data.meta
  }), data.sections.map(function (s) {
    return /*#__PURE__*/React.createElement(SectionRenderer, {
      key: s.id,
      section: s
    });
  })));
}
Object.assign(window, {
  Report: Report
});
