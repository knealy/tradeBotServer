"""
Chart Window GUI for Trading Bot
Displays real-time price charts in a pop-out window using Tkinter and Matplotlib
"""

import tkinter as tk
from tkinter import ttk, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import asyncio
from datetime import datetime
from typing import Optional, List, Dict
import threading


class ChartWindow:
    """Pop-out window for displaying trading charts"""
    
    def __init__(self, trading_bot, symbol: str = 'MNQ', timeframe: str = '5m', limit: int = 100):
        """
        Initialize chart window
        
        Args:
            trading_bot: TopStepXTradingBot instance
            symbol: Trading symbol (default: 'MNQ')
            timeframe: Timeframe (default: '5m')
            limit: Number of bars to display (default: 100)
        """
        self.bot = trading_bot
        self.symbol = symbol
        self.timeframe = timeframe
        self.limit = limit
        self.running = False
        self.refresh_interval = 5  # seconds
        
        # Create main window
        self.root = tk.Tk()
        self.root.title(f"Trading Chart - {symbol} {timeframe}")
        self.root.geometry("1200x700")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Create control panel
        self.create_controls()
        
        # Create chart
        self.create_chart()
        
        # Start refresh loop
        self.start_refresh()
    
    def create_controls(self):
        """Create control panel at the top"""
        control_frame = ttk.Frame(self.root, padding="10")
        control_frame.pack(fill=tk.X)
        
        # Symbol input
        ttk.Label(control_frame, text="Symbol:").grid(row=0, column=0, padx=5)
        self.symbol_var = tk.StringVar(value=self.symbol)
        symbol_entry = ttk.Entry(control_frame, textvariable=self.symbol_var, width=10)
        symbol_entry.grid(row=0, column=1, padx=5)
        
        # Timeframe input
        ttk.Label(control_frame, text="Timeframe:").grid(row=0, column=2, padx=5)
        self.timeframe_var = tk.StringVar(value=self.timeframe)
        timeframe_combo = ttk.Combobox(
            control_frame, 
            textvariable=self.timeframe_var,
            values=['5s', '15s', '30s', '1m', '2m', '5m', '15m', '30m', '1h'],
            width=8
        )
        timeframe_combo.grid(row=0, column=3, padx=5)
        
        # Limit input
        ttk.Label(control_frame, text="Bars:").grid(row=0, column=4, padx=5)
        self.limit_var = tk.StringVar(value=str(self.limit))
        limit_entry = ttk.Entry(control_frame, textvariable=self.limit_var, width=8)
        limit_entry.grid(row=0, column=5, padx=5)
        
        # Refresh button
        refresh_btn = ttk.Button(control_frame, text="Refresh", command=self.refresh_chart)
        refresh_btn.grid(row=0, column=6, padx=5)
        
        # Auto-refresh toggle
        self.auto_refresh_var = tk.BooleanVar(value=True)
        auto_refresh_check = ttk.Checkbutton(
            control_frame, 
            text="Auto-refresh", 
            variable=self.auto_refresh_var
        )
        auto_refresh_check.grid(row=0, column=7, padx=5)
        
        # Status label
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(control_frame, textvariable=self.status_var, foreground="green")
        status_label.grid(row=0, column=8, padx=10)
    
    def create_chart(self):
        """Create matplotlib chart"""
        # Create figure
        self.fig = Figure(figsize=(12, 6), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title(f"{self.symbol} - {self.timeframe}", fontsize=14, fontweight='bold')
        self.ax.set_xlabel("Time")
        self.ax.set_ylabel("Price")
        self.ax.grid(True, alpha=0.3)
        
        # Create canvas
        self.canvas = FigureCanvasTkAgg(self.fig, self.root)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        # Initial empty chart
        self.ax.text(0.5, 0.5, 'Loading chart data...', 
                    horizontalalignment='center', verticalalignment='center',
                    transform=self.ax.transAxes, fontsize=14)
        self.canvas.draw()
    
    def refresh_chart(self):
        """Refresh chart data"""
        try:
            self.status_var.set("Loading...")
            self.root.update()
            
            # Update parameters
            self.symbol = self.symbol_var.get().upper()
            self.timeframe = self.timeframe_var.get()
            try:
                self.limit = int(self.limit_var.get())
            except ValueError:
                self.limit = 100
            
            # Fetch data
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            data = loop.run_until_complete(
                self.bot.get_historical_data(
                    symbol=self.symbol,
                    timeframe=self.timeframe,
                    limit=self.limit
                )
            )
            loop.close()
            
            if not data:
                self.status_var.set("No data")
                return
            
            # Update chart
            self.update_chart(data)
            self.status_var.set(f"Updated: {datetime.now().strftime('%H:%M:%S')}")
            self.root.title(f"Trading Chart - {self.symbol} {self.timeframe}")
            
        except Exception as e:
            self.status_var.set(f"Error: {str(e)}")
            messagebox.showerror("Error", f"Failed to refresh chart: {str(e)}")
    
    def update_chart(self, data: List[Dict]):
        """Update chart with new data"""
        if not data:
            return
        
        # Clear axis
        self.ax.clear()
        
        # Extract data
        timestamps = []
        opens = []
        highs = []
        lows = []
        closes = []
        
        for bar in data:
            # Parse timestamp
            if isinstance(bar.get('timestamp'), str):
                ts = datetime.fromisoformat(bar['timestamp'].replace('Z', '+00:00'))
            else:
                ts = datetime.fromtimestamp(bar.get('timestamp', 0))
            timestamps.append(ts)
            
            opens.append(bar.get('open', 0))
            highs.append(bar.get('high', 0))
            lows.append(bar.get('low', 0))
            closes.append(bar.get('close', 0))
        
        # Plot candlesticks
        for i, (ts, o, h, l, c) in enumerate(zip(timestamps, opens, highs, lows, closes)):
            color = 'green' if c >= o else 'red'
            # High-Low line
            self.ax.plot([ts, ts], [l, h], color='black', linewidth=0.5)
            # Open-Close box
            self.ax.bar(ts, abs(c - o), bottom=min(o, c), color=color, 
                       width=0.0001, alpha=0.8, edgecolor='black', linewidth=0.5)
        
        # Formatting
        self.ax.set_title(f"{self.symbol} - {self.timeframe} ({len(data)} bars)", 
                         fontsize=14, fontweight='bold')
        self.ax.set_xlabel("Time")
        self.ax.set_ylabel("Price")
        self.ax.grid(True, alpha=0.3)
        self.fig.autofmt_xdate()
        
        # Refresh canvas
        self.canvas.draw()
    
    def start_refresh(self):
        """Start auto-refresh loop"""
        self.running = True
        
        def refresh_loop():
            while self.running:
                if self.auto_refresh_var.get():
                    self.root.after(0, self.refresh_chart)
                threading.Event().wait(self.refresh_interval)
        
        thread = threading.Thread(target=refresh_loop, daemon=True)
        thread.start()
    
    def on_closing(self):
        """Handle window close"""
        self.running = False
        self.root.destroy()
    
    def run(self):
        """Run the GUI"""
        self.root.mainloop()


def open_chart_window(trading_bot, symbol: str = 'MNQ', timeframe: str = '5m', limit: int = 100):
    """
    Open a chart window for the trading bot
    
    Args:
        trading_bot: TopStepXTradingBot instance
        symbol: Trading symbol (default: 'MNQ')
        timeframe: Timeframe (default: '5m')
        limit: Number of bars to display (default: 100)
    
    Returns:
        ChartWindow instance
    """
    window = ChartWindow(trading_bot, symbol, timeframe, limit)
    return window

