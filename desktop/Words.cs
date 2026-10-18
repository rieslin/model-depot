using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace ModelDepot
{
    // The sentences the window shows before the page is up, and in the notification area. They are kept with the
    // launcher scripts' sentences in scripts\launcher-strings.json, two columns (Chinese, English). The language is
    // the one chosen in the settings, else the language of Windows, as for the scripts.
    static class Words
    {
        static Dictionary<string, object> table;
        static string configPath;

        public static void Load(string root, string dataDir)
        {
            configPath = Path.Combine(dataDir, "config.json");
            try
            {
                var text = File.ReadAllText(Path.Combine(root, "scripts", "launcher-strings.json"), Encoding.UTF8);
                table = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(text);
            }
            catch { table = new Dictionary<string, object>(); }
        }

        // 0 Chinese, 1 English. Read each time: the language can change in the page while the app runs.
        static int Column()
        {
            string lang = "";
            try
            {
                if (File.Exists(configPath))
                {
                    var cfg = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(configPath, Encoding.UTF8));
                    if (cfg != null && cfg.TryGetValue("language", out var v) && v is string s) lang = s;
                }
            }
            catch { /* unreadable: Windows decides */ }
            if (string.IsNullOrEmpty(lang)) lang = CultureInfo.CurrentUICulture.Name.StartsWith("zh", StringComparison.OrdinalIgnoreCase) ? "zh" : "en";
            return lang == "zh" ? 0 : 1;
        }

        // "zh" or "en": the loading screen's language (it has its own sentences)
        public static string Lang => Column() == 0 ? "zh" : "en";

        public static string T(string key, params object[] args)
        {
            string text = key;
            if (table != null && table.TryGetValue(key, out var row) && row is IList list && list.Count == 2)
                text = list[Column()] as string ?? key;
            return args.Length > 0 ? string.Format(text, args) : text;
        }
    }
}
