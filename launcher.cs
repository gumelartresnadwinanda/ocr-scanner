using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading;

class Program
{
    static bool IsPortInUse(int port)
    {
        try
        {
            using (var client = new TcpClient())
            {
                var result = client.BeginConnect("127.0.0.1", port, null, null);
                bool success = result.AsyncWaitHandle.WaitOne(400);
                if (success && client.Connected)
                {
                    client.EndConnect(result);
                    return true;
                }
            }
        }
        catch { }
        return false;
    }

    static void OpenBrowser(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = url,
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            Console.WriteLine("Could not automatically open browser: " + ex.Message);
        }
    }

    static void Main(string[] args)
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        Directory.SetCurrentDirectory(baseDir);

        Console.Title = "OCR Frame Inspector";
        Console.WriteLine("==================================================");
        Console.WriteLine("             OCR Frame Inspector                  ");
        Console.WriteLine("==================================================");

        string exePath = Path.Combine(baseDir, ".venv", "Scripts", "ocr-scanner-web.exe");
        string url = "http://127.0.0.1:8000";

        if (IsPortInUse(8000))
        {
            Console.WriteLine("[*] Server is already running on http://127.0.0.1:8000");
            Console.WriteLine("[*] Opening web browser...");
            OpenBrowser(url);
            Console.WriteLine("\nReady! Press Enter to close this launcher...");
            Console.ReadLine();
            return;
        }

        Process process = null;
        if (File.Exists(exePath))
        {
            Console.WriteLine("[*] Starting OCR Scanner via virtualenv...");
            process = new Process();
            process.StartInfo = new ProcessStartInfo
            {
                FileName = exePath,
                Arguments = "--host 127.0.0.1 --port 8000",
                WorkingDirectory = baseDir,
                UseShellExecute = false
            };
        }
        else
        {
            Console.WriteLine("[*] Starting OCR Scanner via uv run...");
            process = new Process();
            process.StartInfo = new ProcessStartInfo
            {
                FileName = "uv",
                Arguments = "run ocr-scanner-web --host 127.0.0.1 --port 8000",
                WorkingDirectory = baseDir,
                UseShellExecute = false
            };
        }

        try
        {
            process.Start();
        }
        catch (Exception ex)
        {
            Console.WriteLine("[-] Failed to start service: " + ex.Message);
            Console.WriteLine("Press any key to exit...");
            Console.ReadKey();
            return;
        }

        Console.WriteLine("[*] Waiting for server to initialize...");
        for (int i = 0; i < 30; i++)
        {
            Thread.Sleep(500);
            if (IsPortInUse(8000))
            {
                Console.WriteLine("[+] Server ready on http://127.0.0.1:8000");
                OpenBrowser(url);
                break;
            }
        }

        Console.WriteLine("\n[+] OCR Scanner is running!");
        Console.WriteLine("    Web UI: " + url);
        Console.WriteLine("    Keep this window open while using OCR Scanner.");
        Console.WriteLine("    Press Ctrl+C or close this window to stop the server.\n");

        process.WaitForExit();
    }
}
