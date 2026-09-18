
const { useState, useEffect } = React;

const DEFAULTS = {
  "mode": "dark",
  "display": "default",
  "accent": "default",
  "density": "comfortable"
};

const ACCENTS = {
  default: "#e879f9",
  violet: "#a855f7",
  cyan: "#22d3ee",
  amber: "#fbbf24",
  emerald: "#34d399",
};

function applyTweaks(tweaks) {
  document.querySelectorAll('.vs-theme').forEach((el) => {
    if (tweaks.mode === 'light') el.setAttribute('data-vs-light', '');
    else el.removeAttribute('data-vs-light');
    el.setAttribute('data-vs-density', tweaks.density);
    if (tweaks.display !== 'default') el.setAttribute('data-vs-display', tweaks.display);
    else el.removeAttribute('data-vs-display');
    const accentVal = ACCENTS[tweaks.accent] ?? ACCENTS.default;
    if (tweaks.accent !== 'default') el.style.setProperty('--accent', accentVal);
    else el.style.removeProperty('--accent');
  });
}

function TweaksHost() {
  const [tweaks, setTweaks] = useTweaks(DEFAULTS);
  useEffect(() => { applyTweaks(tweaks); }, [tweaks]);
  useEffect(() => { const id = setTimeout(() => applyTweaks(tweaks), 50); return () => clearTimeout(id); }, []);
  return (
    <TweaksPanel title="Tweaks">
      <TweakSection title="Appearance">
        <TweakRadio label="Mode" value={tweaks.mode} onChange={(v) => setTweaks({ mode: v })}
          options={[{value:'dark', label:'Dark'}, {value:'light', label:'Light'}]} />
        <TweakRadio label="Display font" value={tweaks.display} onChange={(v) => setTweaks({ display: v })}
          options={[{value:'default', label:'Default'}, {value:'sans', label:'Sans'}, {value:'serif', label:'Serif'}]} />
        <TweakRadio label="Density" value={tweaks.density} onChange={(v) => setTweaks({ density: v })}
          options={[{value:'comfortable', label:'Comfortable'}, {value:'compact', label:'Compact'}]} />
      </TweakSection>
      <TweakSection title="Accent">
        <TweakSelect label="Accent" value={tweaks.accent} onChange={(v) => setTweaks({ accent: v })}
          options={[
            {value:'default', label:'Fuchsia (default)'},
            {value:'violet', label:'Violet'},
            {value:'cyan', label:'Cyan'},
            {value:'amber', label:'Amber'},
            {value:'emerald', label:'Emerald'},
          ]} />
      </TweakSection>
    </TweaksPanel>
  );
}

function App() {
  return (
    <>
      <div className="vs-theme vs-theme--a" data-theme-key="a">
        <Report />
      </div>
      <TweaksHost />
    </>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
