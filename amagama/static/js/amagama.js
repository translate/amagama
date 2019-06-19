(function ($) {

  window.MGM = window.MGM || {};

  MGM.search = {

    init: function () {
      $(document).on('submit', '#js-amagama-form', MGM.search.doSearch);
      $.getJSON('/tmserver/languages/', MGM.search.displayLanguages);
      $('#js-search-box').focus();
    },

    doSearch: function (event) {
      event.preventDefault();

      var searchTerms = $('#js-search-box').val();

      // Only trigger the search if query terms have been provided.
      if (searchTerms) {
        //TODO look for a way to specify min_similarity and max_candidates on
        // the search box instead of hardcoding them here.
        var minSimilarity = 30,
            maxCandidates = 20,
            sourceLanguage = $('#js-source-language').val(),
            targetLanguage = $('#js-target-language').val();

        MGM.search.searchTitle = [
          searchTerms, ' (', sourceLanguage, ' → ', targetLanguage, ')'
        ].join('');

        var URLParams = $.param({
          'source': searchTerms,
          'min_similarity': minSimilarity,
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
      $('#js-similar-count').text('Found ' + data.length + ' results.');

      if (data.length) {
        var currentResult,
            resultQuality,
            resultsHTML = [];

        for (var i = 0; i < data.length; i++) {
          currentResult = data[i];

          resultsHTML.push('<tr><td>');
          resultsHTML.push(currentResult.source);
          resultsHTML.push('</td><td>');
          resultsHTML.push(currentResult.target);
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
        langsHTML.push(languages[i]);
        langsHTML.push('">');
        langsHTML.push(languages[i]);
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

}(jQuery));
