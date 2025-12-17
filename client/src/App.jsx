import { useState, useEffect, useRef, useMemo } from 'react'
import { Disc, Play, Square, Activity, Plus, Settings, RefreshCw, Zap, StopCircle, Sliders, Compass } from 'lucide-react'

const EFFECT_OPTIONS = [
  { value: 'sine', label: 'Sine (Periodic)' },
  { value: 'square', label: 'Square' },
  { value: 'triangle', label: 'Triangle' },
  { value: 'sawtoothup', label: 'Sawtooth Up' },
  { value: 'sawtoothdown', label: 'Sawtooth Down' },
  { value: 'constant', label: 'Constant' },
  { value: 'ramp', label: 'Ramp' },
  { value: 'spring', label: 'Spring (Condition)' },
  { value: 'damper', label: 'Damper (Condition)' },
  { value: 'inertia', label: 'Inertia (Condition)' },
  { value: 'friction', label: 'Friction (Condition)' },
  { value: 'leftright', label: 'Left / Right' },
];

const DIRECTION_MODES = [
  { value: 'polar', label: 'Polar (default)' },
  { value: 'cartesian', label: 'Cartesian' },
  { value: 'spherical', label: 'Spherical' },
];

// --- Components ---

const Track = ({ track, onAddClip, onSelectClip }) => {
  return (
    <div className="flex bg-slate-800 border-b border-slate-700 h-24">
      {/* Track Header */}
      <div className="w-48 bg-slate-900 border-r border-slate-700 p-3 flex flex-col justify-center">
        <div className="font-bold text-slate-200">{track.name}</div>
        <div className="text-xs text-slate-500 mt-1">Force: {track.gain}%</div>
      </div>
      {/* Timeline Area */}
      <div className="flex-1 relative bg-slate-800/50 hover:bg-slate-800 transition cursor-crosshair group"
        onClick={() => onAddClip(track.id)}>
        {/* Grid Lines */}
        <div className="absolute inset-0 flex pointer-events-none opacity-10">
          {[...Array(10)].map((_, i) => (
            <div key={i} className="flex-1 border-r border-white"></div>
          ))}
        </div>

        {/* Clips */}
        {track.clips.map(clip => (
          <div key={clip.id}
            className="absolute h-20 top-2 bg-blue-600/80 border border-blue-400 rounded-md overflow-hidden shadow-sm hover:brightness-110 transition"
            style={{ left: `${clip.start}%`, width: `${clip.duration}%` }}
            onDoubleClick={(e) => { e.stopPropagation(); onSelectClip(track.id, clip.id); }}>
            <div className="px-2 py-1 text-xs font-mono text-white truncate">
              {clip.type} {clip.freq}Hz
            </div>
            {/* Fake Waveform */}
            <div className="absolute bottom-0 left-0 right-0 h-8 opacity-50 flex items-end">
              {[...Array(20)].map((_, i) => (
                <div key={i} className="flex-1 bg-white" style={{ height: `${Math.random() * 100}%` }}></div>
              ))}
            </div>
          </div>
        ))}

        {/* Add Prompt */}
        <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 pointer-events-none transition">
          <span className="bg-slate-900/80 text-white text-xs px-2 py-1 rounded">+ Add Effect</span>
        </div>
      </div>
    </div>
  )
}

const DeviceStatus = ({ status, devices, onScan, onConnect }) => {
  return (
    <div className="flex items-center gap-4 bg-slate-950 p-2 rounded-lg border border-slate-800">
      <div className={`flex items-center gap-2 px-3 py-1 rounded-full text-xs font-bold ${status === "Connected" ? "bg-green-900/30 text-green-400 border border-green-800"
          : "bg-red-900/30 text-red-400 border border-red-800"
        }`}>
        <div className={`w-2 h-2 rounded-full ${status === "Connected" ? "bg-green-500 animate-pulse" : "bg-red-500"}`}></div>
        {status}
      </div>

      <div className="h-6 w-px bg-slate-800 mx-2"></div>

      {devices.length === 0 ? (
        <span className="text-slate-500 text-sm flex items-center gap-2">
          <Disc size={16} /> No Devices
        </span>
      ) : (
        devices.map((d, i) => (
          <button key={i} onClick={() => onConnect(d.index)}
            className="flex items-center gap-2 text-sm text-blue-300 hover:text-white transition bg-blue-900/20 px-3 py-1 rounded border border-blue-900 hover:border-blue-500">
            <Disc size={16} /> {d.name}
          </button>
        ))
      )}

      <button onClick={onScan} className="p-2 hover:bg-slate-800 rounded-lg text-slate-400 hover:text-white transition" title="Rescan">
        <RefreshCw size={16} />
      </button>
    </div>
  )
}

// --- Main App ---

const SOCKET_URL = "ws://localhost:8000/ws";

function App() {
  const [status, setStatus] = useState("Disconnected");
  const [devices, setDevices] = useState([]);
  const [logs, setLogs] = useState([]);
  const [tracks, setTracks] = useState([
    { id: 1, name: "Master Force", gain: 100, clips: [] },
    { id: 2, name: "Rumble A", gain: 80, clips: [] },
  ]);
  const [selected, setSelected] = useState(null);

  const ws = useRef(null);

  const selectedClip = useMemo(() => {
    if (!selected) return null;
    const track = tracks.find(t => t.id === selected.trackId);
    if (!track) return null;
    return track.clips.find(c => c.id === selected.clipId) || null;
  }, [selected, tracks]);

  useEffect(() => {
    const connect = () => {
      ws.current = new WebSocket(SOCKET_URL);
      ws.current.onopen = () => setStatus("Connected");
      ws.current.onclose = () => {
        setStatus("Disconnected");
        setTimeout(connect, 3000); // Auto reconnect
      };
      ws.current.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "log") setLogs(prev => [...prev.slice(-20), msg.payload]);
        if (msg.type === "devices") setDevices(msg.payload);
      };
    };
    connect();
    return () => ws.current?.close();
  }, []);

  const selectClip = (trackId, clipId) => {
    setSelected({ trackId, clipId });
  };

  const sendCommand = (cmd, payload) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ cmd, payload }));
    }
  };

  const updateClip = (trackId, clipId, updates) => {
    setTracks(prev => prev.map(t => {
      if (t.id !== trackId) return t;
      return {
        ...t,
        clips: t.clips.map(c => c.id === clipId ? { ...c, ...updates } : c)
      };
    }));
  };

  const updateClipField = (field, value) => {
    if (!selectedClip) return;
    updateClip(selected.trackId, selected.clipId, { [field]: value });
  };

  const updateDirection = (directionPatch) => {
    if (!selectedClip) return;
    updateClip(selected.trackId, selected.clipId, {
      direction: { ...selectedClip.direction, ...directionPatch }
    });
  };

  const serializeClipForServer = (clip) => ({
    type: (clip.type || "sine").toLowerCase(),
    frequency_hz: Number(clip.freq || 50),
    magnitude: Number(clip.magnitude ?? 12000),
    length_ms: Number(clip.lengthMs ?? 1000),
    phase: Number(clip.phase || 0),
    direction_mode: clip.directionMode || "polar",
    direction: clip.direction || {},
    envelope: clip.envelope || {},
    start_mag: clip.startMag ?? -12000,
    end_mag: clip.endMag ?? 12000,
    axes: clip.axes || {},
  });

  const playSelectedClip = () => {
    if (!selectedClip) return;
    sendCommand("play_clip", { clip: serializeClipForServer(selectedClip) });
  };

  const addClip = (trackId) => {
    // Demo: Add a randomized clip
    setTracks(prev => prev.map(t => {
      if (t.id !== trackId) return t;
      const start = Math.random() * 80;
      return {
        ...t,
        clips: [...t.clips, {
          id: Date.now(),
          start,
          duration: 10 + Math.random() * 10,
          type: "sine",
          freq: 50 + Math.floor(Math.random() * 100),
          magnitude: 12000,
          phase: 0,
          lengthMs: 1000,
          directionMode: "polar",
          direction: { angle: 0, radius: 1, x: 1, y: 0, z: 0, yaw: 0, pitch: 0, distance: 1 },
          envelope: { attack_length: 0, attack_level: 0, fade_length: 0, fade_level: 0 },
          startMag: -8000,
          endMag: 8000,
          axes: { x: {}, y: {}, z: {} },
        }]
      }
    }));
  };

  return (
    <div className="flex flex-col h-screen bg-slate-950 text-slate-200 font-sans selection:bg-blue-500/30">

      {/* Top Bar */}
      <header className="h-16 bg-slate-900 border-b border-slate-800 flex items-center justify-between px-6 shadow-xl z-20">
        <div className="flex items-center gap-4">
          <div className="bg-gradient-to-br from-blue-600 to-purple-600 p-2 rounded-lg shadow-lg shadow-blue-500/20">
            <Zap className="text-white" size={24} fill="currentColor" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Fedit <span className="text-blue-500">2.0</span></h1>
        </div>

        <DeviceStatus status={status} devices={devices}
          onScan={() => sendCommand("scan_devices")}
          onConnect={(idx) => sendCommand("connect", { index: idx })} />

        <div className="flex items-center gap-2">
          <button className="bg-slate-800 p-2 rounded text-slate-400 hover:text-white"><Settings size={20} /></button>
        </div>
      </header>

      {/* Toolbar */}
      <div className="h-12 bg-slate-900 border-b border-slate-800 flex items-center px-4 gap-4">
        <div className="flex bg-slate-800 rounded-lg p-1 gap-1">
          <button className="p-1 px-3 bg-green-600 hover:bg-green-500 text-white rounded flex items-center gap-2 text-sm font-semibold transition"
            onClick={() => sendCommand("play_test", { type: "sweep" })}>
            <Play size={16} fill="currentColor" /> Play
          </button>
          <button className="p-1 px-3 hover:bg-slate-700 text-slate-300 rounded flex items-center gap-2 text-sm font-semibold transition"
            onClick={() => sendCommand("stop_all")}>
            <Square size={16} fill="currentColor" /> Stop
          </button>
        </div>
        <div className="w-px h-6 bg-slate-700"></div>
        <span className="text-xs font-mono text-slate-500">00:00:00.000</span>
      </div>

      {/* Main Workspace */}
      <div className="flex-1 flex overflow-hidden">
        {/* Tracks View */}
        <div className="flex-1 overflow-y-auto bg-slate-950 relative">
          {/* Time Ruler */}
          <div className="h-8 bg-slate-900 border-b border-slate-800 sticky top-0 z-10 flex">
            <div className="w-48 border-r border-slate-800 bg-slate-900"></div>
            <div className="flex-1 flex items-end pb-1 px-2 text-xs font-mono text-slate-500">
              <span>0s</span>
              <span className="ml-auto">10s</span>
            </div>
          </div>

          <div className="space-y-[1px] bg-slate-900">
            {tracks.map(t => <Track key={t.id} track={t} onAddClip={addClip} onSelectClip={selectClip} />)}
          </div>

          <div className="p-8 flex justify-center opacity-50 hover:opacity-100 transition">
            <button className="flex items-center gap-2 text-slate-600 hover:text-blue-400 border border-dashed border-slate-700 hover:border-blue-500 p-4 rounded-xl w-full justify-center transition"
              onClick={() => setTracks(prev => [...prev, { id: Date.now(), name: "New Track", gain: 100, clips: [] }])}>
              <Plus size={20} /> Add Track
            </button>
          </div>
        </div>

        {/* Inspector Panel (Right) */}
        <div className="w-80 bg-slate-900 border-l border-slate-800 flex flex-col">
          <div className="p-4 border-b border-slate-800">
            <h3 className="font-semibold text-slate-300 flex items-center gap-2">
              <Activity size={16} /> Properties
            </h3>
          </div>
          <div className="p-4 space-y-4 text-sm text-slate-400">
            {selectedClip ? (
              <div className="bg-slate-800 p-4 rounded-lg space-y-3">
                <div className="flex items-center gap-2 text-slate-100">
                  <Sliders size={14} />
                  <span className="font-semibold">Clip Controls</span>
                </div>

                <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                  Effect Type
                  <select className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                    value={selectedClip.type}
                    onChange={(e) => updateClipField('type', e.target.value)}>
                    {EFFECT_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                </label>

                <div className="grid grid-cols-2 gap-3">
                  <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                    Frequency (Hz)
                    <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                      value={selectedClip.freq}
                      onChange={(e) => updateClipField('freq', parseFloat(e.target.value) || 0)} />
                  </label>
                  <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                    Magnitude
                    <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                      value={selectedClip.magnitude ?? 0}
                      onChange={(e) => updateClipField('magnitude', parseInt(e.target.value) || 0)} />
                  </label>
                  <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                    Length (ms)
                    <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                      value={selectedClip.lengthMs ?? 0}
                      onChange={(e) => updateClipField('lengthMs', parseInt(e.target.value) || 0)} />
                  </label>
                  <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                    Phase (deg)
                    <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                      value={selectedClip.phase ?? 0}
                      onChange={(e) => updateClipField('phase', parseInt(e.target.value) || 0)} />
                  </label>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                    Attack (ms)
                    <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                      value={selectedClip.envelope?.attack_length ?? 0}
                      onChange={(e) => updateClipField('envelope', { ...selectedClip.envelope, attack_length: parseInt(e.target.value) || 0 })} />
                  </label>
                  <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                    Fade (ms)
                    <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                      value={selectedClip.envelope?.fade_length ?? 0}
                      onChange={(e) => updateClipField('envelope', { ...selectedClip.envelope, fade_length: parseInt(e.target.value) || 0 })} />
                  </label>
                </div>

                <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                  Direction Mode
                  <select className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                    value={selectedClip.directionMode}
                    onChange={(e) => updateClipField('directionMode', e.target.value)}>
                    {DIRECTION_MODES.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                  <span className="text-[11px] text-slate-500 flex items-center gap-1"><Compass size={12} /> Polar by default; switch to Cartesian or Spherical to reveal extra axes.</span>
                </label>

                {selectedClip.directionMode === 'polar' && (
                  <div className="grid grid-cols-2 gap-3">
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      Angle (deg)
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.direction?.angle ?? 0}
                        onChange={(e) => updateDirection({ angle: parseInt(e.target.value) || 0 })} />
                    </label>
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      Radius
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.direction?.radius ?? 1}
                        onChange={(e) => updateDirection({ radius: parseInt(e.target.value) || 1 })} />
                    </label>
                  </div>
                )}

                {selectedClip.directionMode === 'cartesian' && (
                  <div className="grid grid-cols-3 gap-3">
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      X Axis
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.direction?.x ?? 1}
                        onChange={(e) => updateDirection({ x: parseInt(e.target.value) || 0 })} />
                    </label>
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      Y Axis
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.direction?.y ?? 0}
                        onChange={(e) => updateDirection({ y: parseInt(e.target.value) || 0 })} />
                    </label>
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      Z Axis
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.direction?.z ?? 0}
                        onChange={(e) => updateDirection({ z: parseInt(e.target.value) || 0 })} />
                    </label>
                  </div>
                )}

                {selectedClip.directionMode === 'spherical' && (
                  <div className="grid grid-cols-3 gap-3">
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      Yaw
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.direction?.yaw ?? 0}
                        onChange={(e) => updateDirection({ yaw: parseInt(e.target.value) || 0 })} />
                    </label>
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      Pitch
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.direction?.pitch ?? 0}
                        onChange={(e) => updateDirection({ pitch: parseInt(e.target.value) || 0 })} />
                    </label>
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      Distance
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.direction?.distance ?? 1}
                        onChange={(e) => updateDirection({ distance: parseInt(e.target.value) || 1 })} />
                    </label>
                  </div>
                )}

                {selectedClip.type === 'ramp' && (
                  <div className="grid grid-cols-2 gap-3">
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      Start Mag
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.startMag}
                        onChange={(e) => updateClipField('startMag', parseInt(e.target.value) || 0)} />
                    </label>
                    <label className="flex flex-col gap-1 text-xs uppercase tracking-wide text-slate-400">
                      End Mag
                      <input type="number" className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-slate-100"
                        value={selectedClip.endMag}
                        onChange={(e) => updateClipField('endMag', parseInt(e.target.value) || 0)} />
                    </label>
                  </div>
                )}

                <div className="flex gap-2 pt-2">
                  <button onClick={playSelectedClip}
                    className="flex-1 bg-green-600 hover:bg-green-500 text-white rounded px-3 py-2 text-sm font-semibold flex items-center gap-2 justify-center">
                    <Play size={14} /> Preview Effect
                  </button>
                  <button onClick={() => sendCommand("stop_all")}
                    className="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-100 rounded px-3 py-2 text-sm font-semibold flex items-center gap-2 justify-center">
                    <StopCircle size={14} /> Stop
                  </button>
                </div>
              </div>
            ) : (
              <div className="bg-slate-800 p-4 rounded-lg">
                <p className="text-slate-300">Select a clip to edit its properties.</p>
                <p className="text-xs text-slate-500 mt-2">Double-click a clip to open it here. Direction defaults to polar; switch to Cartesian to see the Y axis input, or Spherical to work in yaw/pitch.</p>
              </div>
            )}
          </div>

          <div className="mt-auto p-4 border-t border-slate-800">
            <h3 className="font-semibold text-slate-400 mb-2 text-xs uppercase tracking-wider">Console</h3>
            <div className="bg-black rounded-lg p-2 h-32 overflow-y-auto font-mono text-xs text-green-400/80">
              {logs.map((log, i) => <div key={i}>{`> ${log}`}</div>)}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default App
