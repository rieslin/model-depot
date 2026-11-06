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

        // The data folder's config.json as it stands now, or null.
        public static Dictionary<string, object> Config()
        {
            try
            {
                if (configPath != null && File.Exists(configPath))
                    return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(configPath, Encoding.UTF8));
            }
            catch { /* unreadable */ }
            return null;
        }

        // 0 Chinese, 1 English. Read each time: the language can change in the page while the app runs.
        static int Column()
        {
            string lang = "";
            var cfg = Config();
            if (cfg != null && cfg.TryGetValue("language", out var v) && v is string s) lang = s;
            if (string.IsNullOrEmpty(lang)) lang = CultureInfo.CurrentUICulture.Name.StartsWith("zh", StringComparison.OrdinalIgnoreCase) ? "zh" : "en";
            return lang == "zh" ? 0 : 1;
        }

        // "zh" or "en": the loading screen's language
        public static string Lang => Column() == 0 ? "zh" : "en";

        public static string T(string key, params object[] args)
        {
            string text = key;
            if (table != null && table.TryGetValue(key, out var row) && row is IList list && list.Count == 2)
                text = list[Column()] as string ?? key;
            return args.Length > 0 ? string.Format(text, args) : text;
        }

        // Every sentence in the language now, by key, with "lang": what the loading screen is given (LOADER_WORDS).
        public static Dictionary<string, string> All()
        {
            int column = Column();
            var all = new Dictionary<string, string> { { "lang", column == 0 ? "zh" : "en" } };
            if (table != null)
                foreach (var pair in table)
                    if (pair.Value is IList list && list.Count == 2 && list[column] is string text) all[pair.Key] = text;
            return all;
        }
    }

    // The window's advanced settings (group "desktop" of depot\advanced.json beside the program): the shipped value,
    // or this computer's in config.json under "advanced" when it is a number within the shipped bounds, as
    // depot\advanced.py takes it.
    static class Advanced
    {
        // A number of the group "desktop", or null when the shipped file cannot be read.
        public static double? Desktop(string root, string key)
        {
            Dictionary<string, object> entry;
            try
            {
                var shipped = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                    File.ReadAllText(Path.Combine(root, "depot", "advanced.json"), Encoding.UTF8));
                entry = ((Dictionary<string, object>)shipped["desktop"])[key] as Dictionary<string, object>;
                if (entry == null || !Number(entry["value"], out _)) return null;
            }
            catch { return null; }
            Number(entry["value"], out var value);
            var cfg = Words.Config();
            if (cfg != null && cfg.TryGetValue("advanced", out var a) && a is Dictionary<string, object> groups &&
                groups.TryGetValue("desktop", out var g) && g is Dictionary<string, object> mine &&
                mine.TryGetValue(key, out var own) && Number(own, out var n) &&
                (!entry.TryGetValue("min", out var min) || !Number(min, out var low) || n >= low) &&
                (!entry.TryGetValue("max", out var max) || !Number(max, out var high) || n <= high))
                value = n;
            return value;
        }

        static bool Number(object v, out double n)
        {
            n = 0;
            if (v == null || v is bool || v is string) return false;
            try { n = Convert.ToDouble(v, CultureInfo.InvariantCulture); return true; }
            catch { return false; }
        }
    }
}
