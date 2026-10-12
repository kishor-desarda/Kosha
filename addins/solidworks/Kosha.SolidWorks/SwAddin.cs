// SPDX-License-Identifier: Apache-2.0
using System;
using System.Runtime.InteropServices;
using Kosha.CadCore.UI;
using Microsoft.Win32;
using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swpublished;

namespace Kosha.SolidWorks
{
    /// <summary>
    /// SolidWorks add-in entry point. Registers a "Kosha" task pane.
    /// Register with:  RegAsm.exe /codebase Kosha.SolidWorks.dll  (see register.bat)
    /// </summary>
    [ComVisible(true)]
    [Guid("6B1E2C34-7D0A-4C55-9F3E-2B8A51C7D901")]
    [ProgId("Kosha.SolidWorks")]
    public class SwAddin : ISwAddin
    {
        ISldWorks app;
        TaskpaneView pane;
        KoshaTaskPane control;

        public bool ConnectToSW(object ThisSW, int Cookie)
        {
            app = (ISldWorks)ThisSW;
            app.SetAddinCallbackInfo2(0, this, Cookie);
            try
            {
                string icon = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(typeof(SwAddin).Assembly.Location), "kosha.bmp");
                pane = app.CreateTaskpaneView2(System.IO.File.Exists(icon) ? icon : "", "Kosha");
                control = new KoshaTaskPane(new SolidWorksHost(app));
                control.CreateControl();
                pane.DisplayWindowFromHandlex64(control.Handle.ToInt64());
                var events = (SldWorks)app;
                events.ActiveDocChangeNotify += OnActiveDocChange;
                events.FileOpenPostNotify += OnFileOpen;
            }
            catch (Exception ex)
            {
                System.Windows.Forms.MessageBox.Show("Kosha add-in could not start: " + ex.Message, "Kosha");
                return false;
            }
            return true;
        }

        int OnActiveDocChange()
        {
            if (control != null) control.RefreshStatus();
            return 0;
        }

        int OnFileOpen(string fileName)
        {
            if (control != null) control.RefreshStatus();
            return 0;
        }

        // Every handler entered from SolidWorks catches its own errors (development guide 6.4); the pane does the same.

        public bool DisconnectFromSW()
        {
            try
            {
                var events = (SldWorks)app;
                events.ActiveDocChangeNotify -= OnActiveDocChange;
                events.FileOpenPostNotify -= OnFileOpen;
                if (pane != null) pane.DeleteView();
                if (control != null) control.Dispose();
            }
            catch (Exception) { }
            pane = null;
            control = null;
            if (app != null) Marshal.ReleaseComObject(app);
            app = null;
            GC.Collect();
            GC.WaitForPendingFinalizers();
            return true;
        }

        // ------------------------------------------------------------ COM registration
        [ComRegisterFunction]
        public static void Register(Type t)
        {
            string key = @"SOFTWARE\SolidWorks\Addins\{" + t.GUID.ToString() + "}";
            using (var k = Registry.LocalMachine.CreateSubKey(key))
            {
                k.SetValue(null, 1);
                k.SetValue("Title", "Kosha");
                k.SetValue("Description", "Check in/out, revisions and BOMs with the Kosha PLM server");
            }
            using (var k = Registry.CurrentUser.CreateSubKey(@"Software\SolidWorks\AddInsStartup\{" + t.GUID.ToString() + "}"))
            {
                k.SetValue(null, 1, RegistryValueKind.DWord);
            }
        }

        [ComUnregisterFunction]
        public static void Unregister(Type t)
        {
            try { Registry.LocalMachine.DeleteSubKey(@"SOFTWARE\SolidWorks\Addins\{" + t.GUID.ToString() + "}", false); } catch (Exception) { }
            try { Registry.CurrentUser.DeleteSubKey(@"Software\SolidWorks\AddInsStartup\{" + t.GUID.ToString() + "}", false); } catch (Exception) { }
        }
    }
}
