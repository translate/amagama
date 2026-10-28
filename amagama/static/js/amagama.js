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

    label: function (code) {
      var name = MGM.language.name(code);
      return name === code ? code : format('languageOption', {name: name, code: code});
    }
  };

  MGM.search = {

    init: function () {
      $(document).on('submit', '#js-amagama-form', MGM.search.doSearch);
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

          resultsHTML.push('<tr><td>');
          resultsHTML.push(escapeHTML(currentResult.source));
          resultsHTML.push('</td><td>');
          resultsHTML.push(escapeHTML(currentResult.target));
          resultsHTML.push('</td><td>');

          resultQuality = currentResult.quality.toFixed(2);
          if (currentResult.quality === 100.00) {
            resultsHTML.push('<span class="exact-match">');
            resultsHTML.push(resultQuality);
            resultsHTML.push('</span>');
          }
          else {
            resultsHTML.push(resultQuality);
          }
          resultsHTML.push('</td><td>');
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

    populateDropdown: function (languages, $dropdown) {
      var options = $.map(languages, function (code) {
            return {code: code, label: MGM.language.label(code)};
          }),
          langsHTML = [];

      options.sort(function (a, b) {
        return a.label.localeCompare(b.label, MGM.lang);
      });
      for (var i = 0; i < options.length; i++) {
        langsHTML.push('<option value="', escapeHTML(options[i].code), '">',
                       escapeHTML(options[i].label), '</option>');
      }

      $dropdown.html(langsHTML.join(''));
    },

    displayLanguages: function (data) {
      var $sourceLanguage = $('#js-source-language'),
          $targetLanguage = $('#js-target-language'),
          browserLang = navigator.language || navigator.userLanguage;

      MGM.search.populateDropdown(data.source, $sourceLanguage);
      MGM.search.populateDropdown(data.target, $targetLanguage);

      $sourceLanguage.find('option[value="en"]').prop('selected', true);
      $targetLanguage.find('option[value="' + browserLang + '"]')
                     .prop('selected', true);
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
