import { useState, useEffect, useRef } from 'react'
import { Disc, Play, Square, Activity, Plus, Settings, RefreshCw, Zap } from 'lucide-react'

// --- Components ---

const Track = ({ track, onAddClip }) => {
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
            style={{ left: `${clip.start}%`, width: `${clip.duration}%` }}>
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

  const ws = useRef(null);

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

  const sendCommand = (cmd, payload) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ cmd, payload }));
    }
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
          type: "Sine",
          freq: 50 + Math.floor(Math.random() * 100)
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
            {tracks.map(t => <Track key={t.id} track={t} onAddClip={addClip} />)}
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
            <div className="bg-slate-800 p-4 rounded-lg">
              <p>Select a clip to edit its properties.</p>
              <div className="mt-4 space-y-2">
                <div className="h-2 bg-slate-700 rounded w-3/4"></div>
                <div className="h-2 bg-slate-700 rounded w-1/2"></div>
              </div>
            </div>
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
