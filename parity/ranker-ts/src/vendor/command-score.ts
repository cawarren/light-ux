// @ts-nocheck -- upstream file is untyped JS-style TS; kept verbatim.
/*
 * VENDORED FROM cmdk 1.1.1 -- command-score.ts
 *
 * Provenance:
 *   Repository: https://github.com/pacocoursey/cmdk
 *   File:       cmdk/src/command-score.ts
 *   Tag:        v1.1.1 -> commit fb4ea04e9ec211777fbb39c6104e3c5f2ee107d2
 *   Git blob:   bd46cc8b630180832d1a31b577927bfb4813d117
 *   Last change to this file upstream: c7fd231773c36383f11d73464818107814cb02a9 ("lint format");
 *               identical on main at dd2250ed608443e8f32bafc5fa2d1d07a3746aa3 (checked 2026-09-30).
 *   SHA-256 of the upstream file: ccfd0d66e3d31b8197fc4dbb217c9672e7562569775ec3a4f3b7e567eb81dfc7
 *   Published build: npm cmdk@1.1.1 (https://registry.npmjs.org/cmdk/-/cmdk-1.1.1.tgz,
 *               sha512-Vsv7kFaXm+ptHDMZ7izaRsP70GgrW9NBNGswt9OZaVBLlE0SNpDq8eu/VGXyF9r7M0azK3Wy7OlYXsuyYLFzHg==),
 *               dist/chunk-NZJY6EH4.mjs is the minified form of this file.
 *
 * The ONLY modification: the three occurrences of
 *     Math.pow(PENALTY_SKIPPED, n)
 * are replaced by
 *     POW_0999[n]
 * (a frozen table of Math.pow(0.999, n) generated in the pinned Chromium; see
 * parity/tables/pow0999.json). test/vendor-provenance.test.ts reverses that
 * substitution on everything below the BEGIN marker and checks the SHA-256 above.
 * The header (this comment, the @ts-nocheck line and the import) is the only addition.
 *
 * ---------------------------------------------------------------------------
 * MIT License
 *
 * Copyright (c) 2022 Paco Coursey
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
import { POW_0999 } from '../tables.ts'
// ---- BEGIN VERBATIM UPSTREAM (modulo POW_0999) ----
// The scores are arranged so that a continuous match of characters will
// result in a total score of 1.
//
// The best case, this character is a match, and either this is the start
// of the string, or the previous character was also a match.
var SCORE_CONTINUE_MATCH = 1,
  // A new match at the start of a word scores better than a new match
  // elsewhere as it's more likely that the user will type the starts
  // of fragments.
  // NOTE: We score word jumps between spaces slightly higher than slashes, brackets
  // hyphens, etc.
  SCORE_SPACE_WORD_JUMP = 0.9,
  SCORE_NON_SPACE_WORD_JUMP = 0.8,
  // Any other match isn't ideal, but we include it for completeness.
  SCORE_CHARACTER_JUMP = 0.17,
  // If the user transposed two letters, it should be significantly penalized.
  //
  // i.e. "ouch" is more likely than "curtain" when "uc" is typed.
  SCORE_TRANSPOSITION = 0.1,
  // The goodness of a match should decay slightly with each missing
  // character.
  //
  // i.e. "bad" is more likely than "bard" when "bd" is typed.
  //
  // This will not change the order of suggestions based on SCORE_* until
  // 100 characters are inserted between matches.
  PENALTY_SKIPPED = 0.999,
  // The goodness of an exact-case match should be higher than a
  // case-insensitive match by a small amount.
  //
  // i.e. "HTML" is more likely than "haml" when "HM" is typed.
  //
  // This will not change the order of suggestions based on SCORE_* until
  // 1000 characters are inserted between matches.
  PENALTY_CASE_MISMATCH = 0.9999,
  // Match higher for letters closer to the beginning of the word
  PENALTY_DISTANCE_FROM_START = 0.9,
  // If the word has more characters than the user typed, it should
  // be penalised slightly.
  //
  // i.e. "html" is more likely than "html5" if I type "html".
  //
  // However, it may well be the case that there's a sensible secondary
  // ordering (like alphabetical) that it makes sense to rely on when
  // there are many prefix matches, so we don't make the penalty increase
  // with the number of tokens.
  PENALTY_NOT_COMPLETE = 0.99

var IS_GAP_REGEXP = /[\\\/_+.#"@\[\(\{&]/,
  COUNT_GAPS_REGEXP = /[\\\/_+.#"@\[\(\{&]/g,
  IS_SPACE_REGEXP = /[\s-]/,
  COUNT_SPACE_REGEXP = /[\s-]/g

function commandScoreInner(
  string,
  abbreviation,
  lowerString,
  lowerAbbreviation,
  stringIndex,
  abbreviationIndex,
  memoizedResults,
) {
  if (abbreviationIndex === abbreviation.length) {
    if (stringIndex === string.length) {
      return SCORE_CONTINUE_MATCH
    }
    return PENALTY_NOT_COMPLETE
  }

  var memoizeKey = `${stringIndex},${abbreviationIndex}`
  if (memoizedResults[memoizeKey] !== undefined) {
    return memoizedResults[memoizeKey]
  }

  var abbreviationChar = lowerAbbreviation.charAt(abbreviationIndex)
  var index = lowerString.indexOf(abbreviationChar, stringIndex)
  var highScore = 0

  var score, transposedScore, wordBreaks, spaceBreaks

  while (index >= 0) {
    score = commandScoreInner(
      string,
      abbreviation,
      lowerString,
      lowerAbbreviation,
      index + 1,
      abbreviationIndex + 1,
      memoizedResults,
    )
    if (score > highScore) {
      if (index === stringIndex) {
        score *= SCORE_CONTINUE_MATCH
      } else if (IS_GAP_REGEXP.test(string.charAt(index - 1))) {
        score *= SCORE_NON_SPACE_WORD_JUMP
        wordBreaks = string.slice(stringIndex, index - 1).match(COUNT_GAPS_REGEXP)
        if (wordBreaks && stringIndex > 0) {
          score *= POW_0999[wordBreaks.length]
        }
      } else if (IS_SPACE_REGEXP.test(string.charAt(index - 1))) {
        score *= SCORE_SPACE_WORD_JUMP
        spaceBreaks = string.slice(stringIndex, index - 1).match(COUNT_SPACE_REGEXP)
        if (spaceBreaks && stringIndex > 0) {
          score *= POW_0999[spaceBreaks.length]
        }
      } else {
        score *= SCORE_CHARACTER_JUMP
        if (stringIndex > 0) {
          score *= POW_0999[index - stringIndex]
        }
      }

      if (string.charAt(index) !== abbreviation.charAt(abbreviationIndex)) {
        score *= PENALTY_CASE_MISMATCH
      }
    }

    if (
      (score < SCORE_TRANSPOSITION &&
        lowerString.charAt(index - 1) === lowerAbbreviation.charAt(abbreviationIndex + 1)) ||
      (lowerAbbreviation.charAt(abbreviationIndex + 1) === lowerAbbreviation.charAt(abbreviationIndex) && // allow duplicate letters. Ref #7428
        lowerString.charAt(index - 1) !== lowerAbbreviation.charAt(abbreviationIndex))
    ) {
      transposedScore = commandScoreInner(
        string,
        abbreviation,
        lowerString,
        lowerAbbreviation,
        index + 1,
        abbreviationIndex + 2,
        memoizedResults,
      )

      if (transposedScore * SCORE_TRANSPOSITION > score) {
        score = transposedScore * SCORE_TRANSPOSITION
      }
    }

    if (score > highScore) {
      highScore = score
    }

    index = lowerString.indexOf(abbreviationChar, index + 1)
  }

  memoizedResults[memoizeKey] = highScore
  return highScore
}

function formatInput(string) {
  // convert all valid space characters to space so they match each other
  return string.toLowerCase().replace(COUNT_SPACE_REGEXP, ' ')
}

export function commandScore(string: string, abbreviation: string, aliases: string[]): number {
  /* NOTE:
   * in the original, we used to do the lower-casing on each recursive call, but this meant that toLowerCase()
   * was the dominating cost in the algorithm, passing both is a little ugly, but considerably faster.
   */
  string = aliases && aliases.length > 0 ? `${string + ' ' + aliases.join(' ')}` : string
  return commandScoreInner(string, abbreviation, formatInput(string), formatInput(abbreviation), 0, 0, {})
}
