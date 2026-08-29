using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using Antmicro.Renode.Core;
using Antmicro.Renode.Core.CAN;
using Antmicro.Renode.Core.Structure;
using Antmicro.Renode.Logging;
using Antmicro.Renode.Peripherals;

namespace Antmicro.Renode.Peripherals.CAN
{
    public static class TcpCanInjectorExtensions
    {
        public static void CreateTcpCanInjector(this IMachine machine, string name, int port)
        {
            var inj = new TcpCanInjector(port);
            machine.RegisterAsAChildOf(machine.SystemBus, inj, NullRegistrationPoint.Instance);
            machine.SetLocalName(inj, name);
        }
    }

    public class TcpCanInjector : ICAN, IPeripheral
    {
        public TcpCanInjector(int port)
        {
            listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            var t = new Thread(Loop);
            t.IsBackground = true;
            t.Start();
        }

        public void OnFrameReceived(CANMessageFrame message)
        {
            this.Log(LogLevel.Info, "INJECTOR-RX {0}", message);
        }

        public void Reset()
        {
        }

        public void SendFrame(uint id, string hexData)
        {
            var bytes = new byte[hexData.Length / 2];
            for(var i = 0; i < bytes.Length; i++)
            {
                bytes[i] = Convert.ToByte(hexData.Substring(i * 2, 2), 16);
            }
            var fs = FrameSent;
            if(fs == null)
            {
                // Nothing is subscribed, so the frame goes nowhere. This is the failure mode when
                // `connector Connect <injector> <hub>` was never issued, and it is silent unless
                // reported - so report it.
                this.Log(LogLevel.Error, "INJECTOR-TX-DROPPED id=0x{0:X} data={1} (no FrameSent subscriber: is the injector connected to a CAN hub?)", id, hexData);
                return;
            }
            this.Log(LogLevel.Info, "INJECTOR-TX id=0x{0:X} data={1} extended=true", id, hexData);
            fs(new CANMessageFrame(id, bytes, extendedFormat: true));
        }

        private void Loop()
        {
            while(true)
            {
                try
                {
                    var client = listener.AcceptTcpClient();
                    this.Log(LogLevel.Info, "INJECTOR-ACCEPT from {0}", client.Client.RemoteEndPoint);
                    var stream = client.GetStream();
                    var reader = new System.IO.StreamReader(stream);
                    string line;
                    while((line = reader.ReadLine()) != null)
                    {
                        var parts = line.Trim().Split(' ');
                        if(parts.Length != 2)
                        {
                            this.Log(LogLevel.Warning, "INJECTOR-BADLINE '{0}' (expected '<hexid> <hexdata>')", line);
                            continue;
                        }
                        try
                        {
                            SendFrame(Convert.ToUInt32(parts[0], 16), parts[1]);
                        }
                        catch(Exception e)
                        {
                            this.Log(LogLevel.Error, "INJECTOR-PARSE-ERROR on '{0}': {1}", line, e.Message);
                        }
                    }
                    this.Log(LogLevel.Info, "INJECTOR-CLIENT-CLOSED");
                    client.Close();
                }
                catch(Exception e)
                {
                    this.Log(LogLevel.Error, "INJECTOR-LOOP-EXIT: {0}", e.Message);
                    return;
                }
            }
        }

        public event Action<CANMessageFrame> FrameSent;

        private readonly TcpListener listener;
    }
}
