// JsonMini.h
// A tiny, dependency-free JSON reader for the flat string-valued objects used
// by the Mien pipe protocol and vault file. It is intentionally NOT a general
// JSON parser: it extracts top-level "key":"string value" pairs and ignores
// everything else. Malformed input yields an empty map, never a crash.
#pragma once

#include <map>
#include <string>

namespace mien
{

class JsonMini
{
public:
    // Parses a JSON object whose values we care about as strings. Numbers,
    // booleans and null are returned as their literal text. Nested
    // objects/arrays are skipped (their raw text is not returned).
    // Returns false only when the input is not an object at all.
    static bool ParseFlatObject(const std::wstring& text,
                                std::map<std::wstring, std::wstring>& out)
    {
        out.clear();
        size_t i = 0;
        SkipWs(text, i);
        if (i >= text.size() || text[i] != L'{')
        {
            return false;
        }
        ++i;
        for (;;)
        {
            SkipWs(text, i);
            if (i >= text.size())
            {
                return false;
            }
            if (text[i] == L'}')
            {
                ++i;
                return true;
            }
            std::wstring key;
            if (!ParseString(text, i, key))
            {
                return false;
            }
            SkipWs(text, i);
            if (i >= text.size() || text[i] != L':')
            {
                return false;
            }
            ++i;
            SkipWs(text, i);
            std::wstring value;
            if (!ParseValue(text, i, value))
            {
                return false;
            }
            out[key] = value;
            SkipWs(text, i);
            if (i >= text.size())
            {
                return false;
            }
            if (text[i] == L',')
            {
                ++i;
                continue;
            }
            if (text[i] == L'}')
            {
                ++i;
                return true;
            }
            return false;
        }
    }

    static bool GetString(const std::map<std::wstring, std::wstring>& obj,
                          const wchar_t* key, std::wstring& out)
    {
        if (key == nullptr)
        {
            return false;
        }
        auto it = obj.find(key);
        if (it == obj.end())
        {
            return false;
        }
        out = it->second;
        return true;
    }

private:
    static void SkipWs(const std::wstring& t, size_t& i)
    {
        while (i < t.size() && (t[i] == L' ' || t[i] == L'\t' ||
                                t[i] == L'\r' || t[i] == L'\n'))
        {
            ++i;
        }
    }

    // Parses a JSON string starting at the opening quote. Handles \" \\ \/
    // \b \f \n \r \t and \uXXXX (including surrogate pairs).
    static bool ParseString(const std::wstring& t, size_t& i, std::wstring& out)
    {
        out.clear();
        if (i >= t.size() || t[i] != L'"')
        {
            return false;
        }
        ++i;
        while (i < t.size())
        {
            wchar_t c = t[i++];
            if (c == L'"')
            {
                return true;
            }
            if (c == L'\\')
            {
                if (i >= t.size())
                {
                    return false;
                }
                wchar_t e = t[i++];
                switch (e)
                {
                case L'"':  out.push_back(L'"');  break;
                case L'\\': out.push_back(L'\\'); break;
                case L'/':  out.push_back(L'/');  break;
                case L'b':  out.push_back(L'\b'); break;
                case L'f':  out.push_back(L'\f'); break;
                case L'n':  out.push_back(L'\n'); break;
                case L'r':  out.push_back(L'\r'); break;
                case L't':  out.push_back(L'\t'); break;
                case L'u':
                {
                    unsigned int cp = 0;
                    if (!ParseHex4(t, i, cp))
                    {
                        return false;
                    }
                    // Surrogate pair?
                    if (cp >= 0xD800 && cp <= 0xDBFF &&
                        i + 6 <= t.size() && t[i] == L'\\' && t[i + 1] == L'u')
                    {
                        unsigned int lo = 0;
                        size_t save = i;
                        i += 2;
                        if (ParseHex4(t, i, lo) && lo >= 0xDC00 && lo <= 0xDFFF)
                        {
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        }
                        else
                        {
                            i = save; // not a valid pair; emit replacement char
                            cp = 0xFFFD;
                        }
                    }
                    if (cp > 0x10FFFF)
                    {
                        cp = 0xFFFD;
                    }
                    if (cp < 0x10000)
                    {
                        out.push_back(static_cast<wchar_t>(cp));
                    }
                    else
                    {
                        // UTF-16 surrogate pair (wchar_t is 16-bit on Windows)
                        cp -= 0x10000;
                        out.push_back(static_cast<wchar_t>(0xD800 + (cp >> 10)));
                        out.push_back(static_cast<wchar_t>(0xDC00 + (cp & 0x3FF)));
                    }
                    break;
                }
                default:
                    return false;
                }
            }
            else if (c < 0x20)
            {
                return false; // unescaped control character
            }
            else
            {
                out.push_back(c);
            }
        }
        return false; // unterminated
    }

    static bool ParseHex4(const std::wstring& t, size_t& i, unsigned int& out)
    {
        if (i + 4 > t.size())
        {
            return false;
        }
        unsigned int v = 0;
        for (int k = 0; k < 4; ++k)
        {
            wchar_t c = t[i++];
            v <<= 4;
            if (c >= L'0' && c <= L'9')      v |= static_cast<unsigned int>(c - L'0');
            else if (c >= L'a' && c <= L'f') v |= static_cast<unsigned int>(c - L'a' + 10);
            else if (c >= L'A' && c <= L'F') v |= static_cast<unsigned int>(c - L'A' + 10);
            else return false;
        }
        out = v;
        return true;
    }

    // Parses one value: string, or a nested object/array (skipped, value is
    // left empty), or a literal (number/true/false/null -> literal text).
    static bool ParseValue(const std::wstring& t, size_t& i, std::wstring& out)
    {
        out.clear();
        if (i >= t.size())
        {
            return false;
        }
        wchar_t c = t[i];
        if (c == L'"')
        {
            return ParseString(t, i, out);
        }
        if (c == L'{' || c == L'[')
        {
            return SkipNested(t, i); // value stays empty; key still recorded
        }
        // literal: read until , } or whitespace
        size_t start = i;
        while (i < t.size() && t[i] != L',' && t[i] != L'}' &&
               t[i] != L' ' && t[i] != L'\t' && t[i] != L'\r' && t[i] != L'\n')
        {
            ++i;
        }
        if (i == start)
        {
            return false;
        }
        out.assign(t, start, i - start);
        return true;
    }

    static bool SkipNested(const std::wstring& t, size_t& i)
    {
        wchar_t open = t[i];
        wchar_t close = (open == L'{') ? L'}' : L']';
        int depth = 0;
        bool inStr = false;
        while (i < t.size())
        {
            wchar_t c = t[i++];
            if (inStr)
            {
                if (c == L'\\') { ++i; continue; }
                if (c == L'"') inStr = false;
                continue;
            }
            if (c == L'"')      { inStr = true; continue; }
            if (c == open)      { ++depth; continue; }
            if (c == close)
            {
                if (--depth == 0)
                {
                    return true;
                }
            }
        }
        return false;
    }
};

} // namespace mien
