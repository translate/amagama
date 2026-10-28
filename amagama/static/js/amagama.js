(function ($) {

  window.MGM = window.MGM || {};

  function escapeHTML(text) {
    return $('<div>').text(text).html().replace(/"/g, '&quot;');
  }

  // A message from the page's catalog with each {name} replaced by values[name].
  function format(name, values) {
    return MGM.messages[name].replace(/\{(\w+)\}/g, function (match, key) {
      return values.hasOwnProperty(key) ? values[key] : match;
    });
  }

  var displayNames;

  MGM.language = {

    // Intl's language names in the page's language, or null without them.
    displayNames: function () {
      if (displayNames === undefined) {
        try {
          displayNames = new Intl.DisplayNames([MGM.lang], {type: 'language', fallback: 'none'});
        } catch (e) {
          displayNames = null;
        }
      }
      return displayNames;
    },

    // The browser's name for a language in the page's language, else the
    // server's from ISO 639, else its code.
    name: function (code) {
      var described = MGM.languages.described[code] || {},
          names = MGM.language.displayNames(),
          name;
      try {
        name = names && names.of(described.tag || code);
      } catch (e) {
        // A tag Intl can't parse.
      }
      return name || described.name || code;
    },

    // lang and dir attributes for text in a language: its direction is the text's own.
    attributes: function (code) {
      var described = MGM.languages.described[code] || {};
      return ' lang="' + escapeHTML(described.tag || code.replace(/_/g, '-')) + '" dir="auto"';
    },

    label: function (code) {
      var name = MGM.language.name(code);
      return name === code ? code : format('languageOption', {name: name, code: code});
    }
  };

  // localStorage can be missing or refuse access.
  function remembered(key) {
    try {
      return window.localStorage.getItem(key);
    } catch (e) {
      return null;
    }
  }

  function remember(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (e) {
      // Not remembered.
    }
  }

  MGM.search = {

    init: function () {
      $(document).on('submit', '#js-amagama-form', MGM.search.doSearch);
      $(document).on('input', '[data-language-list]', MGM.search.filterLanguages);
      $(document).on('change', '#js-source-language, #js-target-language',
                     MGM.search.rememberLanguages);
      MGM.search.displayLanguages(MGM.languages);
      $('#js-search-box').trigger('focus');
    },

    doSearch: function (event) {
      event.preventDefault();

      var searchTerms = $('#js-search-box').val();

      // Only trigger the search if query terms have been provided.
      if (searchTerms) {
        //TODO look for a way to specify min_similarity and max_candidates on
        // the search box instead of hardcoding them here.
        var maxCandidates = 20,
            sourceLanguage = $('#js-source-language').val(),
            targetLanguage = $('#js-target-language').val();

        if (!sourceLanguage || !targetLanguage) {
          return;
        }
        MGM.search.rememberLanguages();

        MGM.search.sourceAttributes = MGM.language.attributes(sourceLanguage);
        MGM.search.targetAttributes = MGM.language.attributes(targetLanguage);
        MGM.search.searchTitle = format('searchTitle', {
          text: searchTerms,
          source: MGM.language.name(sourceLanguage),
          target: MGM.language.name(targetLanguage)
        });

        var URLParams = $.param({
          'source': searchTerms,
          'max_candidates': maxCandidates
        });

        var queryURL = [
          '/tmserver/', sourceLanguage, '/', targetLanguage, '/unit/?',
          URLParams
        ].join('');

        // Query the amaGama API and display the results.
        $.getJSON(queryURL, MGM.search.displayResults);
      }
    },

    displayResults: function (data) {
      // Titled sources if the list loads; else their names.
      MGM.sources.load().always(function () {
        MGM.search.showResults(data);
      });
    },

    showResults: function (data) {
      $('#js-similar-title').text(MGM.search.searchTitle);
      $('#js-similar-count').text(format('suggestions', {
        count: data.length.toLocaleString(MGM.lang)
      }));

      if (data.length) {
        var currentResult,
            resultQuality,
            resultsHTML = [];

        for (var i = 0; i < data.length; i++) {
          currentResult = data[i];

          // On a span, not the cell, so the text keeps its direction
          // and the column stays lined up with its heading.
          resultsHTML.push('<tr><td><span', MGM.search.sourceAttributes, '>');
          resultsHTML.push(escapeHTML(currentResult.source));
          resultsHTML.push('</span></td><td><span', MGM.search.targetAttributes, '>');
          resultsHTML.push(escapeHTML(currentResult.target));
          resultsHTML.push('</span></td><td>');

          resultQuality = currentResult.quality.toFixed(2);
          if (currentResult.quality === 100.00) {
            resultsHTML.push('<span class="exact-match">');
            resultsHTML.push(resultQuality);
            resultsHTML.push('</span>');
          }
          else {
            resultsHTML.push(resultQuality);
          }
          resultsHTML.push('</td><td class="sources">');
          resultsHTML.push(MGM.sources.titlesHTML(currentResult.sources || []));
          resultsHTML.push('</td></tr>');
        }

        $('#js-similar-results').html(resultsHTML.join(''));
        $('#js-similar-table-headers').show();
      } else {
        $('#js-similar-table-headers').hide();
        $('#js-similar-results').empty();
      }
    },

    populateDropdown: function (languages, $dropdown, selected) {
      var options = $.map(languages, function (code) {
            return {code: code, label: MGM.language.label(code)};
          });

      options.sort(function (a, b) {
        return a.label.localeCompare(b.label, MGM.lang);
      });
      $dropdown.data('options', options);
      MGM.search.showOptions($dropdown, options, selected);
    },

    // Keeps the selected language if it's listed, else selects the first.
    showOptions: function ($dropdown, options, selected) {
      var langsHTML = [];

      selected = selected || $dropdown.val();
      for (var i = 0; i < options.length; i++) {
        langsHTML.push('<option value="', escapeHTML(options[i].code), '">',
                       escapeHTML(options[i].label), '</option>');
      }
      $dropdown.html(langsHTML.join(''));
      if (selected) {
        $dropdown.val(selected);
      }
      if (!$dropdown.val() && options.length) {
        $dropdown.val(options[0].code);
      }
    },

    // Lists only the languages whose name or code has what's typed.
    filterLanguages: function () {
      var $dropdown = $($(this).data('language-list')),
          query = $(this).val().toLocaleLowerCase(MGM.lang);

      MGM.search.showOptions($dropdown, $.grep($dropdown.data('options'), function (option) {
        return option.label.toLocaleLowerCase(MGM.lang).indexOf(query) !== -1;
      }));
    },

    rememberLanguages: function () {
      var source = $('#js-source-language').val(),
          target = $('#js-target-language').val();

      if (source) {
        remember('amagama.sourceLanguage', source);
      }
      if (target) {
        remember('amagama.targetLanguage', target);
      }
    },

    // The first of the wanted languages in the list: each a code, or a
    // BCP 47 tag such as pt-BR, matched as is, then without its last subtags
    // (af-ZA is af).
    preferred: function (codes, wanted) {
      var known = {};

      $.each(codes, function (i, code) {
        var described = MGM.languages.described[code] || {};
        known[code.replace(/_/g, '-')] = code;
        if (described.tag) {
          known[described.tag.toLowerCase()] = code;
        }
      });
      for (var i = 0; i < wanted.length; i++) {
        var subtags = (wanted[i] || '').toLowerCase().replace(/_/g, '-').split('-');
        for (var n = subtags.length; n > 0; n--) {
          var tag = subtags.slice(0, n).join('-');
          if (known.hasOwnProperty(tag)) {
            return known[tag];
          }
        }
      }
      return null;
    },

    displayLanguages: function (data) {
      var browserLanguages = navigator.languages || [navigator.language],
          source = MGM.search.preferred(data.source, [remembered('amagama.sourceLanguage'), 'en']),
          target = MGM.search.preferred(
            data.target, [remembered('amagama.targetLanguage')].concat(browserLanguages));

      MGM.search.populateDropdown(data.source, $('#js-source-language'), source);
      MGM.search.populateDropdown(data.target, $('#js-target-language'), target);
    }
  };

  MGM.sources = {

    // Each source's displayTitle, lang and dir, by name.
    titles: {},

    // The sources list, requested once.
    load: function () {
      if (!MGM.sources.request) {
        MGM.sources.request = $.getJSON('/api/v1/sources/', {lang: MGM.lang})
          .done(function (data) {
            var tag = data.lang.replace(/_/g, '-');
            for (var i = 0; i < data.sources.length; i++) {
              MGM.sources.titles[data.sources[i].name] = {
                title: data.sources[i].displayTitle, lang: tag, dir: data.dir
              };
            }
          });
      }
      return MGM.sources.request;
    },

    titleHTML: function (name) {
      var title = MGM.sources.titles[name];
      if (!title) {
        return escapeHTML(name);
      }
      return ['<span lang="', escapeHTML(title.lang), '" dir="', escapeHTML(title.dir), '">',
              escapeHTML(title.title), '</span>'].join('');
    },

    titlesHTML: function (names) {
      return $.map(names, MGM.sources.titleHTML).join(', ');
    },

    init: function () {
      $('#dataSourcesModal').one('show.bs.modal', function () {
        MGM.sources.load().done(MGM.sources.display);
      });
      $(document).on('input', '#js-sources-filter', MGM.sources.filter);
    },

    display: function (data) {
      var sources = data.sources,
          rowsHTML = [];

      // Without a list, the dialog keeps its general description.
      if (!sources.length) {
        return;
      }
      sources.sort(function (a, b) {
        return a.displayTitle.localeCompare(b.displayTitle, data.lang, {numeric: true});
      });
      for (var i = 0; i < sources.length; i++) {
        var source = sources[i];
        rowsHTML.push('<tr data-name="', escapeHTML(source.name), '"><td>',
                      MGM.sources.titleHTML(source.name), '</td><td>',
                      escapeHTML(source.licence || ''), '</td><td title="',
                      escapeHTML(source.targetLanguages.join(' ')), '">',
                      source.targetLanguages.length, '</td><td>',
                      source.units.toLocaleString(MGM.lang), '</td><td>',
                      escapeHTML(source.imported.slice(0, 10)), '</td></tr>');
      }
      $('#js-sources-results').html(rowsHTML.join(''));
      $('#js-sources-count').text(format('projects', {
        count: sources.length.toLocaleString(MGM.lang)
      }));
      $('#js-sources-fallback').hide();
      $('#js-sources').show();
    },

    filter: function () {
      var query = $(this).val().toLowerCase();

      $('#js-sources-results tr').each(function () {
        var $row = $(this),
            text = ($row.text() + ' ' + $row.data('name')).toLowerCase();
        $row.toggle(text.indexOf(query) !== -1);
      });
    }
  };

}(jQuery));
