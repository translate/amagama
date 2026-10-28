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

  MGM.search = {

    init: function () {
      $(document).on('submit', '#js-amagama-form', MGM.search.doSearch);
      $.getJSON('/tmserver/languages/', MGM.search.displayLanguages);
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
          text: searchTerms, source: sourceLanguage, target: targetLanguage
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
      var langsHTML = [];

      for (var i = 0; i < languages.length; i++) {
        langsHTML.push('<option value="');
        langsHTML.push(escapeHTML(languages[i]));
        langsHTML.push('">');
        langsHTML.push(escapeHTML(languages[i]));
        langsHTML.push('</option>');
      }

      $dropdown.html(langsHTML.join(''));
    },

    displayLanguages: function (data) {
      var $sourceLanguage = $('#js-source-language'),
          $targetLanguage = $('#js-target-language'),
          browserLang = navigator.language || navigator.userLanguage;

      MGM.search.populateDropdown(data.sourceLanguages, $sourceLanguage);
      MGM.search.populateDropdown(data.targetLanguages, $targetLanguage);

      $sourceLanguage.find('option[value="en"]').prop('selected', true);
      $targetLanguage.find('option[value="' + browserLang + '"]')
                     .prop('selected', true);
    }
  };

  MGM.sources = {

    init: function () {
      $('#dataSourcesModal').one('show.bs.modal', function () {
        $.getJSON('/api/v1/sources/', {lang: MGM.lang}, MGM.sources.display);
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
        rowsHTML.push('<tr data-name="', escapeHTML(source.name), '"><td><span lang="',
                      escapeHTML(data.lang), '" dir="', escapeHTML(data.dir), '">',
                      escapeHTML(source.displayTitle), '</span></td><td>',
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
