
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
  const [values, setValues] = React.useState(defaults);
  const setTweak = React.useCallback((key, val) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    window.parent.postMessage({ type: '__edit_mode_set_keys', edits: { [key]: val } }, '*');
  }, []);
  return [values, setTweak];
}
function TweaksPanel({ title = 'Tweaks', children }) {
  const [open, setOpen] = React.useState(false);
  React.useEffect(() => {
    const onMsg = (e) => {
      const t = e?.data?.type;
      if (t === '__activate_edit_mode') setOpen(true);
      else if (t === '__deactivate_edit_mode') setOpen(false);
    };
    window.addEventListener('message', onMsg);
    window.parent.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', onMsg);
  }, []);
  if (!open) return null;
  return (
    <div style={{position:'fixed',right:'16px',bottom:'16px',zIndex:9999,background:'rgba(250,249,247,.95)',borderRadius:'14px',padding:'16px',width:'280px',boxShadow:'0 12px 40px rgba(0,0,0,.18)'}}>
      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'12px'}}>
        <b style={{fontSize:'12px'}}>{title}</b>
        <button onClick={() => setOpen(false)} style={{background:'none',border:'none',cursor:'default',fontSize:'16px'}}>✕</button>
      </div>
      <div>{children}</div>
    </div>
  );
}
function TweakSection({ title, children }) {
  return (<><div style={{fontSize:'10px',fontWeight:600,textTransform:'uppercase',color:'rgba(40,30,20,.45)',padding:'10px 0 6px'}}>{title}</div>{children}</>);
}
function TweakRadio({ label, value, options, onChange }) {
  const opts = options.map((o) => (typeof o === 'object' ? o : { value: o, label: o }));
  return (
    <div style={{marginBottom:'8px'}}>
      <div style={{fontSize:'11px',color:'rgba(40,30,20,.7)',marginBottom:'4px'}}>{label}</div>
      <div style={{display:'flex',gap:'4px'}}>
        {opts.map((o) => (
          <button key={o.value} type="button" onClick={() => onChange(o.value)}
            style={{flex:1,padding:'4px',borderRadius:'6px',border:'.5px solid rgba(0,0,0,.15)',background:o.value===value?'rgba(255,255,255,.9)':'transparent',weight:o.value===value?600:400,textAlign:'center',fontSize:'11px',color:o.value===value?'#29261b':'rgba(41,38,27,.55)',cursor:'default'}}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
function TweakSelect({ label, value, options, onChange }) {
  return (
    <div style={{marginBottom:'8px'}}>
      <div style={{fontSize:'11px',color:'rgba(40,30,20,.7)',marginBottom:'4px'}}>{label}</div>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        style={{width:'100%',padding:'4px',borderRadius:'6px',border:'.5px solid rgba(0,0,0,.15)',background:'rgba(255,255,255,.6)',fontSize:'11px'}}>
        {options.map((o) => {
          const v = typeof o === 'object' ? o.value : o;
          const l = typeof o === 'object' ? o.label : o;
          return <option key={v} value={v}>{l}</option>;
        })}
      </select>
    </div>
  );
}
Object.assign(window, {
  useTweaks, TweaksPanel, TweakSection,
  TweakRadio, TweakSelect,
});
