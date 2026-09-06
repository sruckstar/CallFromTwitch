using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace CallFromTwitch
{
    /// <summary>
    /// Just enough JSON to read what the voice server sends back.
    ///
    /// Written out rather than referenced because the script is one DLL
    /// dropped into GTA V\scripts\, and every dependency is another file the
    /// player has to place correctly for the mod to load at all. Json.NET
    /// would be a 700 KB answer to a list of objects holding strings and
    /// numbers, and System.Web's serializer drags in System.Web.Extensions.
    ///
    /// Strict about structure, forgiving about everything else: an unknown
    /// field is ignored, and a malformed document raises
    /// <see cref="FormatException"/> rather than returning half an answer.
    /// Values come back as string, double, bool, null, List&lt;object&gt; or
    /// Dictionary&lt;string, object&gt;.
    /// </summary>
    internal static class MiniJson
    {
        /// <summary>Parses a document, or throws <see cref="FormatException"/>.</summary>
        public static object Parse(string json)
        {
            if (string.IsNullOrEmpty(json))
                throw new FormatException("empty JSON document");

            int index = 0;
            object value = ParseValue(json, ref index);
            SkipWhitespace(json, ref index);

            if (index < json.Length)
                throw new FormatException("trailing characters after the JSON value");

            return value;
        }

        /// <summary>A field of an object, or null when it is absent or the wrong shape.</summary>
        public static object Field(object node, string name)
        {
            var map = node as Dictionary<string, object>;
            if (map == null)
                return null;

            object value;
            return map.TryGetValue(name, out value) ? value : null;
        }

        /// <summary>A string field, or <paramref name="fallback"/> when absent or null.</summary>
        public static string String(object node, string name, string fallback = "")
        {
            object value = Field(node, name);
            if (value == null)
                return fallback;

            var text = value as string;
            if (text != null)
                return text;

            // A number where a string was expected: coercing beats throwing.
            if (value is double)
                return ((double)value).ToString(CultureInfo.InvariantCulture);

            return fallback;
        }

        /// <summary>A numeric field, or <paramref name="fallback"/>.</summary>
        public static double Number(object node, string name, double fallback = 0)
        {
            object value = Field(node, name);
            if (value is double)
                return (double)value;

            // Some donation services quote their amounts ("100.00").
            var text = value as string;
            double parsed;
            if (text != null && double.TryParse(text, NumberStyles.Float,
                                                CultureInfo.InvariantCulture, out parsed))
                return parsed;

            return fallback;
        }

        /// <summary>An array field, or an empty list.</summary>
        public static List<object> Array(object node, string name)
        {
            var list = Field(node, name) as List<object>;
            return list ?? new List<object>();
        }

        private static object ParseValue(string json, ref int index)
        {
            SkipWhitespace(json, ref index);
            if (index >= json.Length)
                throw new FormatException("unexpected end of JSON");

            switch (json[index])
            {
                case '{': return ParseObject(json, ref index);
                case '[': return ParseArray(json, ref index);
                case '"': return ParseString(json, ref index);
                case 't': return ParseLiteral(json, ref index, "true", true);
                case 'f': return ParseLiteral(json, ref index, "false", false);
                case 'n': return ParseLiteral(json, ref index, "null", null);
                default: return ParseNumber(json, ref index);
            }
        }

        private static Dictionary<string, object> ParseObject(string json, ref int index)
        {
            var map = new Dictionary<string, object>(StringComparer.Ordinal);
            index++;                                  // past the '{'
            SkipWhitespace(json, ref index);

            if (index < json.Length && json[index] == '}')
            {
                index++;
                return map;
            }

            while (true)
            {
                SkipWhitespace(json, ref index);
                if (index >= json.Length || json[index] != '"')
                    throw new FormatException("expected a key string in a JSON object");

                string key = ParseString(json, ref index);

                SkipWhitespace(json, ref index);
                if (index >= json.Length || json[index] != ':')
                    throw new FormatException("expected a colon after a JSON key");
                index++;

                map[key] = ParseValue(json, ref index);

                SkipWhitespace(json, ref index);
                if (index >= json.Length)
                    throw new FormatException("unterminated JSON object");

                if (json[index] == ',')
                {
                    index++;
                    continue;
                }

                if (json[index] == '}')
                {
                    index++;
                    return map;
                }

                throw new FormatException("expected a comma or closing brace in a JSON object");
            }
        }

        private static List<object> ParseArray(string json, ref int index)
        {
            var list = new List<object>();
            index++;                                  // past the '['
            SkipWhitespace(json, ref index);

            if (index < json.Length && json[index] == ']')
            {
                index++;
                return list;
            }

            while (true)
            {
                list.Add(ParseValue(json, ref index));

                SkipWhitespace(json, ref index);
                if (index >= json.Length)
                    throw new FormatException("unterminated JSON array");

                if (json[index] == ',')
                {
                    index++;
                    continue;
                }

                if (json[index] == ']')
                {
                    index++;
                    return list;
                }

                throw new FormatException("expected a comma or closing bracket in a JSON array");
            }
        }

        private static string ParseString(string json, ref int index)
        {
            index++;                                  // past the opening quote
            var sb = new StringBuilder();

            while (index < json.Length)
            {
                char c = json[index++];

                if (c == '"')
                    return sb.ToString();

                if (c != '\\')
                {
                    sb.Append(c);
                    continue;
                }

                if (index >= json.Length)
                    break;

                char escape = json[index++];
                switch (escape)
                {
                    case '"': sb.Append('"'); break;
                    case '\\': sb.Append('\\'); break;
                    case '/': sb.Append('/'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'u':
                        if (index + 4 > json.Length)
                            throw new FormatException("truncated unicode escape in a JSON string");

                        // Surrogate pairs need no special handling: each half
                        // is its own escape, and appending both rebuilds the
                        // character.
                        sb.Append((char)ushort.Parse(json.Substring(index, 4),
                                                     NumberStyles.HexNumber,
                                                     CultureInfo.InvariantCulture));
                        index += 4;
                        break;
                    default:
                        throw new FormatException("unknown escape in a JSON string");
                }
            }

            throw new FormatException("unterminated JSON string");
        }

        private static double ParseNumber(string json, ref int index)
        {
            int start = index;

            while (index < json.Length && "+-.eE0123456789".IndexOf(json[index]) >= 0)
                index++;

            if (index == start)
                throw new FormatException("expected a JSON value at position " + start);

            double parsed;
            if (!double.TryParse(json.Substring(start, index - start), NumberStyles.Float,
                                 CultureInfo.InvariantCulture, out parsed))
                throw new FormatException("malformed number in JSON");

            return parsed;
        }

        private static object ParseLiteral(string json, ref int index, string literal, object value)
        {
            if (index + literal.Length > json.Length ||
                string.CompareOrdinal(json, index, literal, 0, literal.Length) != 0)
                throw new FormatException("expected the literal " + literal + " in JSON");

            index += literal.Length;
            return value;
        }

        private static void SkipWhitespace(string json, ref int index)
        {
            while (index < json.Length)
            {
                char c = json[index];
                if (c != ' ' && c != '\t' && c != '\r' && c != '\n')
                    return;
                index++;
            }
        }
    }
}
