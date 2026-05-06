//+------------------------------------------------------------------+
//|                                          MT5_SignalBot.mq5      |
//|                            SMC Engine v3 — Signal Bridge EA      |
//|                                                                  |
//|  Drop into MQL5/Experts/, compile (F7), attach to ANY chart on   |
//|  your MT5, enable AutoTrading. Polls signals.json every          |
//|  PollIntervalSec and opens trades for every new signal it finds. |
//+------------------------------------------------------------------+
#property copyright "SMC Engine v3"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//── Inputs ─────────────────────────────────────────────────────────
input string  SignalFilePath     = "signals.json";
input double  RiskPctPerTrade    = 1.0;
input int     MaxOpenPositions   = 5;
input double  MaxDailyDDPct      = 3.0;
input int     PollIntervalSec    = 5;
input int     MagicNumber        = 20260428;
input int     MaxSlippagePoints  = 30;
input bool    DemoOnly           = true;

//── Globals ────────────────────────────────────────────────────────
CTrade   trade;
double   sessionStartBalance = 0;
string   processedSignals[];

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(MaxSlippagePoints);
   sessionStartBalance = AccountInfoDouble(ACCOUNT_BALANCE);

   bool demo = (AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO);
   if(DemoOnly && !demo)
   {
      Alert("SMC Bot: DemoOnly=true but account is LIVE. Disable DemoOnly to proceed.");
      return(INIT_FAILED);
   }
   PrintFormat("SMC Engine EA initialized. Balance=%.2f %s | Mode=%s",
               sessionStartBalance, AccountInfoString(ACCOUNT_CURRENCY),
               demo ? "DEMO" : "LIVE");
   EventSetTimer(PollIntervalSec);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) { EventKillTimer(); }

//+------------------------------------------------------------------+
void OnTimer()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double ddPct  = (sessionStartBalance - equity) / sessionStartBalance * 100.0;
   if(ddPct >= MaxDailyDDPct) { Print("Daily DD limit hit. Pausing."); return; }
   if(CountBotPositions() >= MaxOpenPositions) return;

   ProcessSignalFile();
   ManageOpenPositions();
}

//+------------------------------------------------------------------+
int CountBotPositions()
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(PositionSelectByTicket(tk) && PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         n++;
   }
   return(n);
}

//+------------------------------------------------------------------+
void ProcessSignalFile()
{
   if(!FileIsExist(SignalFilePath)) return;
   int handle = FileOpen(SignalFilePath, FILE_READ|FILE_TXT|FILE_ANSI|FILE_SHARE_READ);
   if(handle == INVALID_HANDLE) return;

   string content = "";
   while(!FileIsEnding(handle))
      content += FileReadString(handle) + "\n";
   FileClose(handle);

   string asset      = JsonGetString(content, "asset");
   string dir        = JsonGetString(content, "direction");
   double entry      = JsonGetDouble(content, "entry");
   double sl         = JsonGetDouble(content, "sl_price");
   double tp1        = JsonGetDouble(content, "tp1");
   double tp2        = JsonGetDouble(content, "tp2");
   double tp3        = JsonGetDouble(content, "tp3");
   string timestamp  = JsonGetString(content, "timestamp");

   if(asset == "" || dir == "" || entry == 0 || sl == 0) return;

   string key = timestamp + "_" + asset;
   int n = ArraySize(processedSignals);
   for(int k = 0; k < n; k++)
      if(processedSignals[k] == key) return;
   ArrayResize(processedSignals, n + 1);
   processedSignals[n] = key;

   string symbol = ResolveSymbol(asset);
   if(symbol == "") { PrintFormat("Symbol map miss for %s", asset); return; }

   ExecuteTrade(symbol, dir, entry, sl, tp1, tp2, tp3);
}

//+------------------------------------------------------------------+
string ResolveSymbol(string asset)
{
   StringReplace(asset, "/", "");
   if(SymbolSelect(asset, true)) return(asset);

   string suffix1 = asset + "r";
   string suffix2 = asset + "x";
   string suffix3 = asset + ".r";
   string suffix4 = asset + "m";
   if(SymbolSelect(suffix1, true)) return(suffix1);
   if(SymbolSelect(suffix2, true)) return(suffix2);
   if(SymbolSelect(suffix3, true)) return(suffix3);
   if(SymbolSelect(suffix4, true)) return(suffix4);

   // FxPro friendly names
   if(asset == "XAUUSD" && SymbolSelect("GOLD",     true)) return("GOLD");
   if(asset == "BTCUSD" && SymbolSelect("BITCOIN",  true)) return("BITCOIN");
   if(asset == "ETHUSD" && SymbolSelect("ETHEREUM", true)) return("ETHEREUM");

   return("");
}

//+------------------------------------------------------------------+
void ExecuteTrade(string symbol, string dir, double entry, double sl,
                  double tp1, double tp2, double tp3)
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmt = balance * (RiskPctPerTrade / 100.0);
   double slDist  = MathAbs(entry - sl);
   if(slDist <= 0) return;

   double tickVal  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double pointVal = tickVal / tickSize;
   double lot      = riskAmt / (slDist * pointVal);

   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double minL = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   lot = MathMax(minL, MathMin(maxL, MathRound(lot / step) * step));
   lot = NormalizeDouble(lot, 2);

   double price = (dir == "LONG") ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                                  : SymbolInfoDouble(symbol, SYMBOL_BID);
   bool ok;
   if(dir == "LONG") ok = trade.Buy(lot, symbol, price, sl, 0, "SMC v3");
   else              ok = trade.Sell(lot, symbol, price, sl, 0, "SMC v3");

   if(ok)
   {
      ulong ticket = trade.ResultOrder();
      PrintFormat("Filled %s %s %.2f lots @ %.5f (#%I64u)",
                  dir, symbol, lot, trade.ResultPrice(), ticket);
      PlacePartialTPs(symbol, dir, lot, tp1, tp2, tp3);
   }
   else
   {
      PrintFormat("Order failed: %s %s — %s", dir, symbol, trade.ResultComment());
   }
}

//+------------------------------------------------------------------+
void PlacePartialTPs(string symbol, string dir, double totalLot,
                     double tp1, double tp2, double tp3)
{
   double pcts[];   ArrayResize(pcts, 3);
   double prices[]; ArrayResize(prices, 3);
   string labels[]; ArrayResize(labels, 3);
   pcts[0]   = 0.35; pcts[1]   = 0.40; pcts[2]   = 0.25;
   prices[0] = tp1;  prices[1] = tp2;  prices[2] = tp3;
   labels[0] = "TP1"; labels[1] = "TP2"; labels[2] = "TP3";

   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   double minL = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);

   ENUM_ORDER_TYPE pt = (dir == "LONG") ? ORDER_TYPE_SELL_LIMIT : ORDER_TYPE_BUY_LIMIT;

   for(int i = 0; i < 3; i++)
   {
      double v = MathMax(minL, MathRound((totalLot * pcts[i]) / step) * step);
      v = NormalizeDouble(v, 2);

      MqlTradeRequest req;
      MqlTradeResult  res;
      ZeroMemory(req);
      ZeroMemory(res);

      req.action       = TRADE_ACTION_PENDING;
      req.symbol       = symbol;
      req.volume       = v;
      req.type         = pt;
      req.price        = prices[i];
      req.magic        = MagicNumber;
      req.type_time    = ORDER_TIME_GTC;
      req.type_filling = ORDER_FILLING_RETURN;
      req.comment      = labels[i] + " SMC v3";

      if(OrderSend(req, res) && res.retcode == TRADE_RETCODE_DONE)
         PrintFormat("  %s placed: %.2f @ %.5f", labels[i], v, prices[i]);
      else
         PrintFormat("  %s failed: %s", labels[i], res.comment);
   }
}

//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong tk = PositionGetTicket(i);
      if(!PositionSelectByTicket(tk)) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      string sym = PositionGetString(POSITION_SYMBOL);
      ENUM_POSITION_TYPE pt = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double openPrc = PositionGetDouble(POSITION_PRICE_OPEN);
      double curSL   = PositionGetDouble(POSITION_SL);
      double slDist  = MathAbs(openPrc - curSL);
      double target  = (pt == POSITION_TYPE_BUY) ? openPrc + 1.5 * slDist
                                                  : openPrc - 1.5 * slDist;
      double cur     = (pt == POSITION_TYPE_BUY) ? SymbolInfoDouble(sym, SYMBOL_BID)
                                                  : SymbolInfoDouble(sym, SYMBOL_ASK);
      bool reached   = (pt == POSITION_TYPE_BUY) ? (cur >= target) : (cur <= target);
      bool alreadyBE = (pt == POSITION_TYPE_BUY) ? (curSL >= openPrc)
                                                  : (curSL <= openPrc && curSL != 0);
      if(reached && !alreadyBE)
         trade.PositionModify(tk, openPrc, PositionGetDouble(POSITION_TP));
   }
}

//+------------------------------------------------------------------+
//| Tiny JSON helpers                                                 |
//+------------------------------------------------------------------+
string JsonGetString(string s, string key)
{
   string p = "\"" + key + "\"";
   int i = StringFind(s, p);
   if(i < 0) return("");
   i = StringFind(s, "\"", i + StringLen(p) + 1);
   if(i < 0) return("");
   int j = StringFind(s, "\"", i + 1);
   if(j < 0) return("");
   return(StringSubstr(s, i + 1, j - i - 1));
}

double JsonGetDouble(string s, string key)
{
   string p = "\"" + key + "\"";
   int i = StringFind(s, p);
   if(i < 0) return(0);
   i = StringFind(s, ":", i) + 1;
   while(i < StringLen(s))
   {
      ushort c = StringGetCharacter(s, i);
      if(c != ' ' && c != '"') break;
      i++;
   }
   int j = i;
   while(j < StringLen(s))
   {
      ushort c = StringGetCharacter(s, j);
      if((c < '0' || c > '9') && c != '.' && c != '-') break;
      j++;
   }
   return(StringToDouble(StringSubstr(s, i, j - i)));
}
//+------------------------------------------------------------------+
